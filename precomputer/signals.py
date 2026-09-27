# Copyright The IETF Trust 2026, All Rights Reserved
"""Re-precompute the curated files when staff change one.

Ratings, subscriptions and set entries only mark their document in
PendingDocumentChange; push_document_changes drains the marks on a schedule. A
task per reader write would be thousands a day. The models a person edits
deliberately enqueue a run directly.

Enqueued with a countdown so that saving a subject and its assignments in one sitting
usually lands as one run rather than several, and on_commit so that a rolled-back
admin save does not publish a change that never happened.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from docsets.models import DocumentSetEntry
from ratings.models import Rating
from subjects.models import Subject, SubjectAlias, SubjectAssignment
from subscriptions.models import Subscription
from surveys.models import Survey

from .models import PendingDocumentChange

logger = logging.getLogger("reef")

# Long enough to absorb a few edits in a row, short enough that staff see their
# change published while they are still looking at it.
CURATED_DEBOUNCE_SECONDS = 60


def _schedule_curated(sender, **kwargs):
    from .tasks import precompute_curated

    def enqueue():
        try:
            precompute_curated.apply_async(countdown=CURATED_DEBOUNCE_SECONDS)
        except Exception:
            # The scheduled runs still cover this; losing the prompt refresh is
            # a smaller harm than a 500 on a save that otherwise succeeded.
            logger.warning(
                "Could not enqueue precompute after %s change",
                sender.__name__,
                exc_info=True,
            )

    transaction.on_commit(enqueue)


for model in (Subject, SubjectAlias, SubjectAssignment, Survey):
    receiver(post_save, sender=model, dispatch_uid=f"precompute_{model.__name__}_save")(
        _schedule_curated
    )
    receiver(
        post_delete, sender=model, dispatch_uid=f"precompute_{model.__name__}_delete"
    )(_schedule_curated)


def _mark_document(doc):
    """Record doc's change once the write is committed."""
    if not doc:
        return

    def mark():
        try:
            # Saves an existing row too, moving last_seen.
            PendingDocumentChange.objects.update_or_create(doc=doc)
        except Exception:
            # A delayed refresh is preferable to failing a reader write that
            # already succeeded.
            logger.warning("Could not record a change to %s", doc, exc_info=True)

    transaction.on_commit(mark)


def _rating_changed(sender, instance, **kwargs):
    _mark_document(instance.rfc)


def _set_entry_changed(sender, instance, **kwargs):
    _mark_document(instance.doc)


def _subscription_changed(sender, instance, **kwargs):
    # Only rfc subscriptions count towards a document.
    if instance.kind == Subscription.Kind.RFC:
        _mark_document((instance.params or {}).get("rfc"))


for model, handler in (
    (Rating, _rating_changed),
    (DocumentSetEntry, _set_entry_changed),
    (Subscription, _subscription_changed),
):
    receiver(post_save, sender=model, dispatch_uid=f"mark_{model.__name__}_save")(
        handler
    )
    receiver(post_delete, sender=model, dispatch_uid=f"mark_{model.__name__}_delete")(
        handler
    )
