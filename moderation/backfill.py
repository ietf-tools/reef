# Copyright The IETF Trust 2026, All Rights Reserved
from docsets.models import DocumentSet
from surveys.models import Response

from . import jev
from .models import ModerationFlag
from .tasks import classify_content

# What one click on the moderation page queues. The tasks run as fast as the workers
# take them, so this is what keeps a click inside the classifier's rate limit.
BATCH_SIZE = 50

TARGETS = (("documentset", DocumentSet), ("response", Response))


def _unscored(target_type, model):
    scored = ModerationFlag.objects.filter(
        target_type=target_type, classifier=jev.CLASSIFIER
    ).values_list("target_id", flat=True)
    return model.objects.exclude(pk__in=list(scored)).values_list("pk", flat=True)


def queue_unscored(limit):
    """Queue scoring for live sets and responses with no score from the current model.

    Returns (queued, remaining). Items queued but not yet scored are still unscored,
    so a second call before the workers catch up queues them again.
    """
    queued = 0
    remaining = 0
    for target_type, model in TARGETS:
        pending = _unscored(target_type, model)
        for pk in pending[: max(limit - queued, 0)]:
            classify_content.delay(target_type, str(pk))
            queued += 1
        remaining += pending.count()
    return queued, remaining - queued
