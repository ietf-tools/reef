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
queuing events for every years-old document newly categorized -- see the
"Assigning subjects at scale" and "Assignment as an event" open items in plan.md.
Admin assignments do fire, each row of the subject's assignment inline included; the
daily digest folds however many were saved into one mail per reader.
"""

import logging
from collections import defaultdict

from django.conf import settings
from django.db import transaction
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
    if not created:
        return

    def notify():
        from .models import Subscription
        from .tasks import stage_subject_event

        # Matched on the subject and its ancestors, not through the document: a
        # reader following HTTP learns nothing from the same document also becoming
        # Security, which subscriptions_for_document would tell them.
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
        for user_id, subscription_ids in subscription_ids_by_user.items():
            stage_subject_event(
                user_id,
                subscription_ids,
                "subject_assignment",
                f"subject-assignment:{instance.pk}",
                event,
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
