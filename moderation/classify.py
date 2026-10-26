# Copyright The IETF Trust 2026, All Rights Reserved
"""Score content for spam and record it on a flag.

A score is a recorded fact about a piece of text. Whether it raises a flag is a
threshold set in the environment, and with none set every score is only recorded:
shadow mode, for measuring the classifier against moderators' labels before it is
allowed to put anything in front of them.
"""

from django.conf import settings

from docsets.models import DocumentSet
from surveys.models import Response

from . import jev, review
from .models import ModerationFlag

TARGETS = {
    "documentset": (DocumentSet, review.set_text, lambda obj: obj.owner),
    "response": (Response, review.response_text, lambda obj: obj.submitted_by),
}


def enabled():
    return bool(settings.REEF_JEV_API_KEY)


def score(target_type, target_id):
    """Score one set or response if it has not been scored as it now reads.

    Returns the flag written, or None when there was nothing to do: the content is
    gone, has no text, or already holds a score for this exact text.
    """
    model, text_of, owner_of = TARGETS[target_type]
    obj = model.objects.filter(pk=target_id).first()
    if obj is None:
        return None
    text = text_of(obj)
    if not text.strip():
        return None
    existing = ModerationFlag.objects.filter(
        target_type=target_type, target_id=str(target_id), classifier=jev.CLASSIFIER
    ).first()
    if existing is not None and existing.text == text:
        return None

    probability = jev.spam_probability(text, settings.REEF_JEV_API_KEY)
    threshold = settings.REEF_SPAM_FLAG_THRESHOLD
    flag, _ = ModerationFlag.objects.update_or_create(
        target_type=target_type,
        target_id=str(target_id),
        classifier=jev.CLASSIFIER,
        defaults={
            "subject_user": owner_of(obj),
            "text": text,
            "score": probability,
            "raised": threshold is not None and probability >= threshold,
        },
    )
    return flag
