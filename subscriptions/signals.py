# Copyright The IETF Trust 2026, All Rights Reserved
"""Notify subject subscribers when a document is newly tagged.

The daily change run only sees Red's published index, so tagging an existing
document in Reef's admin produces no event there. SubjectAssignment's post_save is
the one place that fact exists.

created=True only: unassigning corrects the vocabulary rather than reporting news
about the document, so it earns no line. (Red's lost relations are reported --
"No longer obsoleted by" -- because there the relation is a fact about the
document.)

Bulk writes fire no post_save, so import_assignments and import_subjects notify
nobody: a subscription is for documents newly given the subject, not for a
back-catalogue backfill categorizing years-old RFCs. A merge moves documents with
bulk_create too, so the target's followers are not told about the arrivals; the
source's followers hear about the merge itself. Admin assignments do fire, inline
rows included, and the daily digest folds them into one mail per reader.
"""

import logging
from collections import defaultdict

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from subjects.models import Subject, SubjectAssignment

logger = logging.getLogger("reef")


def _document_url(doc):
    """Red's canonical page for the document, matching changes.DocumentChange.url."""
    base = settings.REEF_RFC_SITE_URL.rstrip("/")
    return f"{base}/info/{doc}/"


@receiver(
    post_save, sender=SubjectAssignment, dispatch_uid="notify_new_subject_assignment"
)
def _notify_new_assignment(sender, instance, created, **kwargs):
    # raw is a fixture load replaying rows, not staff tagging a document.
    if not created or kwargs.get("raw"):
        return

    def notify():
        from .models import Subscription
        from .tasks import stage_subject_event

        # Match on the subject and its ancestors, not the document: a reader
        # following HTTP learns nothing from the same document also becoming Security.
        subject = instance.subject
        ancestor_ids = Subject.all_objects.filter(
            path__in=subject.ancestor_paths
        ).values_list("pk", flat=True)
        subscriptions = Subscription.objects.filter(
            kind=Subscription.Kind.SUBJECT,
            subject_id__in=[subject.pk, *ancestor_ids],
        )
        if not subscriptions:
            return

        event = {
            "doc": instance.doc,
            "change": f"Added to the subject {instance.subject.name}.",
            "url": _document_url(instance.doc),
        }
        # One row per reader: following both Email and Messaging is one line with
        # two reasons, and a second row would break the (user, kind, key) constraint.
        subscription_ids_by_user = defaultdict(list)
        for subscription in subscriptions:
            subscription_ids_by_user[subscription.user_id].append(subscription.pk)
        # Keyed on the fact, not the row, so a tag removed and re-added before the
        # digest is one line: the constraint refuses the second stage. The savepoint
        # keeps that refusal from poisoning an enclosing transaction.
        event_key = f"subject-assignment:{subject.pk}:{instance.doc}"
        for user_id, subscription_ids in subscription_ids_by_user.items():
            try:
                with transaction.atomic():
                    stage_subject_event(
                        user_id,
                        subscription_ids,
                        "subject_assignment",
                        event_key,
                        event,
                    )
            except IntegrityError:
                logger.info(
                    "Assignment of %s to %s already staged for user %s",
                    instance.doc,
                    subject.slug,
                    user_id,
                )

    def notify_guarded():
        # The save has already committed. An exception here would 500 it and make
        # Django drop the commit hooks queued behind this one.
        try:
            notify()
        except Exception:
            logger.warning(
                "Could not stage a notification for assignment %s",
                instance.pk,
                exc_info=True,
            )

    transaction.on_commit(notify_guarded)
