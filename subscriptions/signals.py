# Copyright The IETF Trust 2026, All Rights Reserved
"""Notify subject subscribers when staff tag a document in the admin.

The daily change run only sees Red's published index, so tagging an existing
document in Reef's admin produces no event there. SubjectAssignment's post_save is
the one place that fact exists. The staging itself is subscriptions.tagging, shared
with the subject sync, which stages from its own diff because its bulk writes never
reach this receiver.

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

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from subjects.models import SubjectAssignment

logger = logging.getLogger("reef")


@receiver(
    post_save, sender=SubjectAssignment, dispatch_uid="notify_new_subject_assignment"
)
def _notify_new_assignment(sender, instance, created, **kwargs):
    # raw is a fixture load replaying rows, not staff tagging a document.
    if not created or kwargs.get("raw"):
        return

    def notify_guarded():
        from .tagging import stage_assignment_events

        # The save has already committed. An exception here would 500 it and make
        # Django drop the commit hooks queued behind this one.
        try:
            stage_assignment_events([(instance.subject_id, instance.doc)])
        except Exception:
            logger.warning(
                "Could not stage a notification for assignment %s",
                instance.pk,
                exc_info=True,
            )

    transaction.on_commit(notify_guarded)
