# Copyright The IETF Trust 2026, All Rights Reserved
"""Moderators' verdicts: spam takes content down, fine records that it was seen."""

from django.db import transaction
from django.utils import timezone

from . import actions
from .models import ModerationFlag

Verdict = ModerationFlag.Verdict


def reviewed_target_ids(target_type):
    """Ids of what a moderator has given a verdict, as the strings the label holds."""
    return list(
        ModerationFlag.objects.filter(
            target_type=target_type,
            classifier="",
            verdict__in=list(Verdict.values),
        ).values_list("target_id", flat=True)
    )


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def set_text(document_set):
    return "\n".join(
        part for part in (document_set.title, document_set.description) if part
    )


def response_text(response):
    return "\n".join(part for part in _strings(response.data) if part.strip())


def _label(reviewer, target_type, target_id, owner, text, verdict):
    ModerationFlag.objects.update_or_create(
        target_type=target_type,
        target_id=str(target_id),
        classifier="",
        defaults={
            "subject_user": owner,
            "text": text,
            "verdict": verdict,
            "reviewed_by": reviewer,
            "reviewed_at": timezone.now(),
        },
    )


@transaction.atomic
def review_sets(reviewer, sets, verdict, reason=""):
    """Label each set; spam also takes it down. Returns how many were labelled."""
    count = 0
    for document_set in sets.select_related("owner"):
        _label(
            reviewer,
            "documentset",
            document_set.pk,
            document_set.owner,
            set_text(document_set),
            verdict,
        )
        if verdict == Verdict.SPAM:
            actions.take_down_set(reviewer, document_set, reason)
        count += 1
    return count


@transaction.atomic
def review_responses(reviewer, responses, verdict, reason=""):
    """Label each response; spam also removes it. Returns how many were labelled."""
    count = 0
    for response in responses.select_related("submitted_by"):
        _label(
            reviewer,
            "response",
            response.pk,
            response.submitted_by,
            response_text(response),
            verdict,
        )
        count += 1
    if verdict == Verdict.SPAM:
        actions.delete_responses(reviewer, responses, reason)
    return count
