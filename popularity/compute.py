# Copyright The IETF Trust 2026, All Rights Reserved
"""The one writer of DocumentPopularity.

Each source is a callable returning ``{rfc: score}`` with scores in [0, 1]. A
document's popularity is the mean of the sources that rank it; with one source that
is the source's own score. A document no source ranks has no row.
"""

import dataclasses

from django.db import transaction
from django.utils import timezone

from .models import DocumentPopularity, MatomoRanking


def matomo_scores():
    return dict(MatomoRanking.objects.values_list("rfc", "score"))


SOURCES = (matomo_scores,)


@dataclasses.dataclass
class RecomputeResult:
    ranked: int
    deleted: int


def replace_ranking(model, scores):
    """Make model's rows exactly `scores`, keeping created_at where a row survives.

    An upsert plus a delete rather than delete-all-and-insert, so that created_at
    still says when a document first entered the ranking. The timestamps are set
    here because auto_now does not apply through bulk_create.
    """
    now = timezone.now()
    with transaction.atomic():
        deleted, _ = model.objects.exclude(rfc__in=scores).delete()
        model.objects.bulk_create(
            [
                model(rfc=rfc, score=score, created_at=now, updated_at=now)
                for rfc, score in scores.items()
            ],
            update_conflicts=True,
            unique_fields=["rfc"],
            update_fields=["score", "updated_at"],
        )
    return deleted


def recompute_popularity():
    combined = {}
    for source in SOURCES:
        for rfc, score in source().items():
            combined.setdefault(rfc, []).append(score)
    scores = {rfc: sum(values) / len(values) for rfc, values in combined.items()}
    deleted = replace_ranking(DocumentPopularity, scores)
    return RecomputeResult(ranked=len(scores), deleted=deleted)
