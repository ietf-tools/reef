# Copyright The IETF Trust 2026, All Rights Reserved
"""Queue new and edited content for scoring once the write is committed."""

import logging

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from docsets.models import DocumentSet
from surveys.models import Response

logger = logging.getLogger("reef")

# A set is saved for its updated_at whenever a document is added to it, which says
# nothing new about its text.
_SET_TEXT_FIELDS = frozenset({"title", "description"})


def _enqueue(target_type, target_id):
    from .tasks import classify_content

    def enqueue():
        try:
            classify_content.delay(target_type, str(target_id))
        except Exception:
            # The write already succeeded, and the backfill finds what was missed.
            logger.warning(
                "Could not queue %s %s for scoring",
                target_type,
                target_id,
                exc_info=True,
            )

    transaction.on_commit(enqueue)


@receiver(post_save, sender=DocumentSet, dispatch_uid="score_documentset")
def set_saved(sender, instance, update_fields=None, **kwargs):
    if not settings.REEF_JEV_API_KEY:
        return
    if update_fields is not None and not _SET_TEXT_FIELDS & set(update_fields):
        return
    _enqueue("documentset", instance.pk)


@receiver(post_save, sender=Response, dispatch_uid="score_response")
def response_saved(sender, instance, created, **kwargs):
    # A response is never edited, so only its creation has text to score.
    if created and settings.REEF_JEV_API_KEY:
        _enqueue("response", instance.pk)
