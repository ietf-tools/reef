# Copyright The IETF Trust 2026, All Rights Reserved
"""Notify subject subscribers when a document is newly tagged.

The daily change run (subscriptions.tasks.detect_rfc_changes) only sees Red's
published index, so tagging an existing, unchanged document with a subject in
Reef's own admin produces no event there -- nothing in Red's index moved. This is
the other half: a signal on SubjectAssignment, the write that decides "this
document now carries this subject", which is the only place that fact exists.

post_save with created=True only, not post_delete: unassigning is a curation
correction -- a tag that should not have been there -- rather than news about the
document, so it earns no line. That is a narrower rule than Red's relations get,
where losing one is reported ("No longer obsoleted by"), because there the relation
is a fact about the document rather than about the vocabulary.

Deliberately does not fire for a bulk assignment. import_assignments and
import_subjects both write with bulk_create, which sends no post_save signal, and
that is what keeps a back-catalogue backfill of the roughly 9,800 RFCs from
queuing events for every years-old document newly categorized -- see the
"Assigning subjects at scale" and "Assignment as an event" open items in plan.md.
Admin assignments do fire, each row of the subject's assignment inline included; the
daily digest folds however many were saved into one mail per reader. A merge moves
the source's documents onto the target with bulk_create as well, so the target's
existing followers are not told about the arrivals: the documents were already
categorized, only re-filed. The source's followers hear about the merge itself, from
subjects.merge.notify_merge.
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
        # Keyed on the fact, not the row: a tag removed and re-added before the
        # digest runs is one line, because the unique constraint refuses the
        # second stage. The savepoint keeps that refusal from poisoning any
        # transaction this callback happens to run inside.
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
