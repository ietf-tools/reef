# Copyright The IETF Trust 2026, All Rights Reserved
"""Notifying subject subscribers from SubjectAssignment.save(), not from the diff."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.db import DatabaseError, transaction
from django.test import TestCase

from subjects.models import Subject, SubjectAssignment

from .models import SubjectNotificationEvent, Subscription

User = get_user_model()


class NewAssignmentNotificationTests(TestCase):
    def setUp(self):
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

        notification = SubjectNotificationEvent.objects.get()
        self.assertEqual(notification.user_id, self.user.pk)
        self.assertEqual(notification.subscription_ids, [subscription.pk])
        self.assertEqual(notification.event["change"], "Added to the subject Security.")
        self.assertEqual(notification.event["doc"], "rfc9110")

    def test_a_subscriber_to_a_covering_ancestor_is_also_notified(self):
        parent = Subject.objects.create(name="Messaging", slug="messaging")
        child = Subject.objects.create(name="Email", slug="email", parent=parent)
        self.follow(parent)
        with self.captureOnCommitCallbacks(execute=True):
            SubjectAssignment.objects.create(subject=child, doc="rfc9110")

        self.assertEqual(SubjectNotificationEvent.objects.count(), 1)

    def test_a_follower_of_another_subject_on_the_document_is_not_notified(self):
        """The document already carried HTTP. Tagging it Security is news to
        Security's followers; nothing about HTTP changed."""
        http = Subject.objects.create(name="HTTP", slug="http")
        SubjectAssignment.objects.create(subject=http, doc="rfc9110")
        self.follow(http)
        with self.captureOnCommitCallbacks(execute=True):
            SubjectAssignment.objects.create(subject=self.subject, doc="rfc9110")

        self.assertEqual(SubjectNotificationEvent.objects.count(), 0)

    def test_a_subscriber_to_an_unrelated_subject_is_not_notified(self):
        other = Subject.objects.create(name="Routing", slug="routing")
        self.follow(other)
        with self.captureOnCommitCallbacks(execute=True):
            SubjectAssignment.objects.create(subject=self.subject, doc="rfc9110")

        self.assertEqual(SubjectNotificationEvent.objects.count(), 0)

    def test_an_rfc_subscriber_is_not_renotified_by_a_tagging(self):
        """A change to the document, not a change to what it is tagged with, is
        what an rfc subscription is about."""
        Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        with self.captureOnCommitCallbacks(execute=True):
            SubjectAssignment.objects.create(subject=self.subject, doc="rfc9110")

        self.assertEqual(SubjectNotificationEvent.objects.count(), 0)

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

        notification = SubjectNotificationEvent.objects.get()
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

        self.assertEqual(SubjectNotificationEvent.objects.count(), 0)

    def test_unassigning_notifies_nobody(self):
        """Removing an assignment is a correction to the vocabulary, not news
        about the document."""
        self.follow(self.subject)
        with self.captureOnCommitCallbacks(execute=True):
            assignment = SubjectAssignment.objects.create(
                subject=self.subject, doc="rfc9110"
            )
        SubjectNotificationEvent.objects.all().delete()

        with self.captureOnCommitCallbacks(execute=True):
            assignment.delete()

        self.assertEqual(SubjectNotificationEvent.objects.count(), 0)

    def test_a_staging_failure_does_not_fail_the_save(self):
        """The assignment has already committed when the hook runs; a missing
        digest line is logged rather than turned into a 500."""
        self.follow(self.subject)
        with (
            mock.patch(
                "subscriptions.tasks.stage_subject_event",
                side_effect=DatabaseError("down"),
            ),
            self.assertLogs("reef", level="WARNING") as logs,
            self.captureOnCommitCallbacks(execute=True),
        ):
            SubjectAssignment.objects.create(subject=self.subject, doc="rfc9110")

        self.assertIn("Could not stage a notification", logs.output[0])
        self.assertEqual(SubjectNotificationEvent.objects.count(), 0)

    def test_a_rolled_back_assignment_notifies_nobody(self):
        self.follow(self.subject)
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                SubjectAssignment.objects.create(subject=self.subject, doc="rfc9110")
                transaction.set_rollback(True)

        self.assertEqual(SubjectNotificationEvent.objects.count(), 0)
        self.assertFalse(SubjectAssignment.objects.exists())
