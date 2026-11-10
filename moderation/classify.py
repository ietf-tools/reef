# Copyright The IETF Trust 2026, All Rights Reserved
"""Score content for spam and record it on a flag.

A score is a recorded fact about a piece of text. Whether it puts the content in
front of moderators is a comparison with jev.SPAM_THRESHOLD, made when the page is
shown.
"""

import urllib.error

from django.conf import settings

from docsets.models import DocumentSet
from surveys.models import Response

from . import jev, review
from .models import ClassifierFailure, ModerationFlag

TARGETS = {
    "documentset": (DocumentSet, review.set_text, lambda obj: obj.owner),
    "response": (Response, review.response_text, lambda obj: obj.submitted_by),
}


def enabled():
    return bool(settings.REEF_JEV_API_KEY)


def describe_failure(exc):
    """What went wrong, in words a moderator can act on and nothing from the content."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"the classifier answered HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"the classifier could not be reached: {exc.reason}"
    if isinstance(exc, TimeoutError):
        return "the classifier timed out"
    return "the classifier gave an answer that could not be read"


def record_failure(target_type, target_id, exc):
    failure, created = ClassifierFailure.objects.get_or_create(
        target_type=target_type,
        target_id=str(target_id),
        defaults={"detail": describe_failure(exc)[:255]},
    )
    if not created:
        failure.detail = describe_failure(exc)[:255]
        failure.attempts += 1
        failure.save()


def score(target_type, target_id):
    """Score one set or response if it has not been scored as it now reads.

    Content with no text is scored 0 without asking the classifier: it holds no
    spam, and a score is what stops it counting as unscored.

    Returns the flag written, or None when there was nothing to do: the content is
    gone or already holds a score for this exact text. Either way the content is no
    longer waiting on a failed attempt.
    """
    flag = _score(target_type, target_id)
    ClassifierFailure.objects.filter(
        target_type=target_type, target_id=str(target_id)
    ).delete()
    return flag


def _score(target_type, target_id):
    model, text_of, owner_of = TARGETS[target_type]
    obj = model.objects.filter(pk=target_id).first()
    if obj is None:
        return None
    text = text_of(obj)
    existing = ModerationFlag.objects.filter(
        target_type=target_type, target_id=str(target_id), classifier=jev.CLASSIFIER
    ).first()
    if existing is not None and existing.text == text:
        return None

    probability = (
        jev.spam_probability(text, settings.REEF_JEV_API_KEY) if text.strip() else 0.0
    )
    flag, _ = ModerationFlag.objects.update_or_create(
        target_type=target_type,
        target_id=str(target_id),
        classifier=jev.CLASSIFIER,
        defaults={
            "subject_user": owner_of(obj),
            "text": text,
            "score": probability,
        },
    )
    return flag
