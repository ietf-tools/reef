# Copyright The IETF Trust 2026, All Rights Reserved
"""Notify subject subscribers when a document is newly tagged.

The daily change run (subscriptions.tasks.detect_rfc_changes) only sees Red's
published index, so tagging an existing, unchanged document with a subject in
Reef's own admin produces no event there -- nothing in Red's index moved. This is
the other half: a signal on SubjectAssignment, the write that decides "this
document now carries this subject", which is the only place that fact exists.

post_save with created=True only, not post_delete: unassigning is a correction to
the vocabulary rather than news about the document, in the same way Red losing an
obsoleted_by entry is described but does not warrant its own mail (see
subscriptions/changes.py::render_change).

Deliberately does not fire for a bulk assignment. import_assignments and
import_subjects both write with bulk_create, which sends no post_save signal, and
that is what keeps a back-catalogue backfill of the roughly 9,800 RFCs from
mailing every subscriber about years-old documents newly categorized -- see the
"Assigning subjects at scale" and "Assignment as an event" open items in plan.md.
Only assignment through the admin, one document at a time, notifies.
"""

import logging
from collections import defaultdict

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from reef.docids import display_doc_id
from subjects.models import SubjectAssignment

logger = logging.getLogger("reef")


def _document_url(doc):
    """Red's canonical page for the document, matching changes.DocumentChange.url."""
    base = settings.REEF_RFC_SITE_URL.rstrip("/")
    return f"{base}/info/{doc}/"


@receiver(
    post_save, sender=SubjectAssignment, dispatch_uid="notify_new_subject_assignment"
)
def _notify_new_assignment(sender, instance, created, **kwargs):
    if not created:
        return

    def notify():
        from .matching import subscriptions_for_document
        from .models import Subscription
        from .tasks import queue_notification

        # subscriptions_for_document already expands to every subject covering the
        # document, ancestors included; kind=SUBJECT excludes the rfc and set
        # subscribers it also matches, who are not being told anything new.
        subscriptions = subscriptions_for_document(instance.doc).filter(
            kind=Subscription.Kind.SUBJECT
        )
        if not subscriptions:
            return

        event = {
            "doc": instance.doc,
            "doc_display": display_doc_id(instance.doc),
            "change": f"Added to the subject {instance.subject.name}.",
            "url": _document_url(instance.doc),
        }
        # Grouped by reader before enqueuing, not queued once per subscription: a
        # reader following both the assigned subject and a covering ancestor
        # matches twice, and notification_key hashes (user, scope, events) rather
        # than the subscription, so two calls for one reader would collide on the
        # same dedupe_key and the second insert would raise.
        subscription_ids_by_user = defaultdict(list)
        for subscription in subscriptions:
            subscription_ids_by_user[subscription.user_id].append(subscription.pk)
        for user_id, subscription_ids in subscription_ids_by_user.items():
            queue_notification(
                user_id,
                subscription_ids,
                [event],
                scope=f"subject-assignment:{instance.pk}",
            )

    transaction.on_commit(notify)
