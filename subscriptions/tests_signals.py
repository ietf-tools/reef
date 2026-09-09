# Copyright The IETF Trust 2026, All Rights Reserved
"""Notifying subject subscribers from SubjectAssignment.save(), not from the diff."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase

from reef.testing import document_meta as meta
from reef.testing import stub_rfc_index
from subjects.models import Subject, SubjectAssignment

from .models import PendingNotification, Subscription

User = get_user_model()


class NewAssignmentNotificationTests(TestCase):
    def setUp(self):
        stub_rfc_index(self, {"rfc9110": meta()})
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="reader@example.org"
        )
        self.subject = Subject.objects.create(name="Security", slug="security")

    def follow(self, subject, user=None):
        return Subscription.objects.create(
            user=user or self.user, kind=Subscription.Kind.SUBJECT, subject=subject
        )

    def test_a_subscriber_is_notified_when_a_document_is_newly_tagged(self):
        subscription = self.follow(self.subject)
        with self.captureOnCommitCallbacks(execute=True):
            SubjectAssignment.objects.create(subject=self.subject, doc="rfc9110")

        notification = PendingNotification.objects.get()
        self.assertEqual(notification.user_id, self.user.pk)
        self.assertEqual(notification.subscription_ids, [subscription.pk])
        self.assertEqual(
            notification.events[0]["change"], "Added to the subject Security."
        )
        self.assertEqual(notification.events[0]["doc"], "rfc9110")

    def test_a_subscriber_to_a_covering_ancestor_is_also_notified(self):
        parent = Subject.objects.create(name="Messaging", slug="messaging")
        child = Subject.objects.create(name="Email", slug="email", parent=parent)
        self.follow(parent)
        with self.captureOnCommitCallbacks(execute=True):
            SubjectAssignment.objects.create(subject=child, doc="rfc9110")

        self.assertEqual(PendingNotification.objects.count(), 1)

    def test_a_subscriber_to_an_unrelated_subject_is_not_notified(self):
        other = Subject.objects.create(name="Routing", slug="routing")
        self.follow(other)
        with self.captureOnCommitCallbacks(execute=True):
            SubjectAssignment.objects.create(subject=self.subject, doc="rfc9110")

        self.assertEqual(PendingNotification.objects.count(), 0)

    def test_an_rfc_subscriber_is_not_renotified_by_a_tagging(self):
        """A change to the document, not a change to what it is tagged with, is
        what an rfc subscription is about."""
        Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        with self.captureOnCommitCallbacks(execute=True):
            SubjectAssignment.objects.create(subject=self.subject, doc="rfc9110")

        self.assertEqual(PendingNotification.objects.count(), 0)

    def test_a_reader_following_both_the_subject_and_its_ancestor_is_told_once(self):
        """Two matching subscriptions for one reader must not collide on the
        dedupe key, which is hashed over the reader and the event rather than
        the subscription."""
        parent = Subject.objects.create(name="Messaging", slug="messaging")
        child = Subject.objects.create(name="Email", slug="email", parent=parent)
        first = self.follow(parent)
        second = self.follow(child)
        with self.captureOnCommitCallbacks(execute=True):
            SubjectAssignment.objects.create(subject=child, doc="rfc9110")

        notification = PendingNotification.objects.get()
        self.assertEqual(
            sorted(notification.subscription_ids), sorted([first.pk, second.pk])
        )

    def test_a_bulk_assignment_notifies_nobody(self):
        """import_assignments and import_subjects both write this way, which is
        what keeps a back-catalogue backfill from mailing every subscriber about
        years-old documents newly categorized."""
        self.follow(self.subject)
        with self.captureOnCommitCallbacks(execute=True):
            SubjectAssignment.objects.bulk_create(
                [SubjectAssignment(subject=self.subject, doc="rfc9110")]
            )

        self.assertEqual(PendingNotification.objects.count(), 0)

    def test_unassigning_notifies_nobody(self):
        """Removing an assignment is a correction to the vocabulary, not news
        about the document."""
        self.follow(self.subject)
        with self.captureOnCommitCallbacks(execute=True):
            assignment = SubjectAssignment.objects.create(
                subject=self.subject, doc="rfc9110"
            )
        PendingNotification.objects.all().delete()

        with self.captureOnCommitCallbacks(execute=True):
            assignment.delete()

        self.assertEqual(PendingNotification.objects.count(), 0)

    def test_a_rolled_back_assignment_notifies_nobody(self):
        self.follow(self.subject)
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                SubjectAssignment.objects.create(subject=self.subject, doc="rfc9110")
                transaction.set_rollback(True)

        self.assertEqual(PendingNotification.objects.count(), 0)
        self.assertFalse(SubjectAssignment.objects.exists())
