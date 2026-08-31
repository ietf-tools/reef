# Copyright The IETF Trust 2026, All Rights Reserved
"""Retiring and merging: taking a subject out of use without cutting anybody off."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from reef.testing import stub_rfc_index
from subjects.merge import MergeError, merge_and_notify, merge_subjects
from subjects.models import Subject, SubjectAssignment
from subscriptions.models import PendingNotification, Subscription

User = get_user_model()


class RetireTests(TestCase):
    def setUp(self):
        self.subject = Subject.objects.create(name="Security", slug="security")

    def test_a_retired_subject_leaves_the_default_manager(self):
        self.subject.retire()
        self.assertFalse(Subject.objects.filter(pk=self.subject.pk).exists())
        self.assertTrue(Subject.all_objects.filter(pk=self.subject.pk).exists())

    def test_retiring_is_undone_by_unretiring(self):
        """Retired means retired until somebody says otherwise."""
        self.subject.retire()
        self.subject.unretire()
        self.assertTrue(Subject.objects.filter(pk=self.subject.pk).exists())
        self.assertIsNone(self.subject.merged_into)

    def test_a_retired_subject_keeps_matching_for_its_followers(self):
        """The whole reason to retire rather than delete. A relation filter joins on
        the foreign key rather than going through the default manager, so a
        subscription still reaches the subject it points at."""
        stub_rfc_index(self)
        user = User.objects.create(username="u", oidc_sub="s")
        SubjectAssignment.objects.create(subject=self.subject, doc="rfc9110")
        Subscription.objects.create(
            user=user, kind=Subscription.Kind.SUBJECT, subject=self.subject
        )
        self.subject.retire()
        from subscriptions.tasks import subscriptions_for_document

        self.assertEqual(len(list(subscriptions_for_document("rfc9110"))), 1)


class MergeTests(TestCase):
    def setUp(self):
        self.source = Subject.objects.create(name="Security", slug="security")
        self.target = Subject.objects.create(
            name="Security and privacy", slug="security-and-privacy"
        )
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="r@example.org"
        )

    def follow(self, subject, user=None):
        return Subscription.objects.create(
            user=user or self.user, kind=Subscription.Kind.SUBJECT, subject=subject
        )

    def test_documents_move_to_the_target(self):
        SubjectAssignment.objects.create(subject=self.source, doc="rfc9110")
        merge_subjects(self.source, self.target)
        self.assertEqual(
            list(self.target.assignments.values_list("doc", flat=True)), ["rfc9110"]
        )
        self.assertEqual(self.source.assignments.count(), 0)

    def test_a_document_under_both_is_not_duplicated(self):
        for subject in (self.source, self.target):
            SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        merge_subjects(self.source, self.target)
        self.assertEqual(self.target.assignments.count(), 1)

    def test_followers_are_repointed(self):
        subscription = self.follow(self.source)
        merge_subjects(self.source, self.target)
        subscription.refresh_from_db()
        self.assertEqual(subscription.subject, self.target)

    def test_somebody_following_both_keeps_one_subscription(self):
        """unique(user, kind, params, set, subject) will not hold two identical
        rows, so repointing blindly would raise."""
        self.follow(self.source)
        kept = self.follow(self.target)
        affected = merge_subjects(self.source, self.target)
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertEqual(affected, [kept.pk])

    def test_the_source_is_retired_and_points_at_the_target(self):
        merge_subjects(self.source, self.target)
        self.source.refresh_from_db()
        self.assertTrue(self.source.is_retired)
        self.assertEqual(self.source.merged_into, self.target)

    def test_a_subject_cannot_be_merged_into_itself(self):
        with self.assertRaises(MergeError):
            merge_subjects(self.source, self.source)

    def test_merging_into_a_retired_subject_is_refused(self):
        """It would strand the followers somewhere nobody is offered."""
        self.target.retire()
        with self.assertRaises(MergeError):
            merge_subjects(self.source, self.target)

    def test_merging_an_already_retired_subject_is_refused(self):
        self.source.retire()
        with self.assertRaises(MergeError):
            merge_subjects(self.source, self.target)

    def test_everybody_affected_is_told(self):
        self.follow(self.source)
        other = User.objects.create(username="o", oidc_sub="o", email="o@example.org")
        self.follow(self.source, user=other)
        with self.captureOnCommitCallbacks(execute=True):
            merge_and_notify(self.source, self.target)
        self.assertEqual(PendingNotification.objects.count(), 2)
        notification = PendingNotification.objects.first()
        self.assertIn("Security", notification.events[0]["change"])
        self.assertIn("Security and privacy", notification.events[0]["change"])

    def test_somebody_following_both_is_told_once(self):
        """Their subscription changed meaning even though it was not the one that
        moved, so leaving them out would be the silent change all over again."""
        self.follow(self.source)
        self.follow(self.target)
        with self.captureOnCommitCallbacks(execute=True):
            merge_and_notify(self.source, self.target)
        self.assertEqual(PendingNotification.objects.count(), 1)

    def test_the_notice_carries_no_document(self):
        """It is news about the vocabulary rather than about an RFC, which the
        digest template and subject line already handle."""
        self.follow(self.source)
        with self.captureOnCommitCallbacks(execute=True):
            merge_and_notify(self.source, self.target)
        self.assertEqual(PendingNotification.objects.get().events[0]["doc"], "")


class RetiredSubjectApiTests(APITestCase):
    def setUp(self):
        self.subject = Subject.objects.create(name="Security", slug="security")
        SubjectAssignment.objects.create(subject=self.subject, doc="rfc9110")
        self.target = Subject.objects.create(
            name="Security and privacy", slug="security-and-privacy"
        )

    def test_a_live_subject_is_listed_and_says_it_is_not_retired(self):
        listing = self.client.get("/api/reef/subjects/").json()
        self.assertEqual(
            [s["slug"] for s in listing], ["security", "security-and-privacy"]
        )
        detail = self.client.get("/api/reef/subjects/security/").json()
        self.assertFalse(detail["retired"])
        self.assertEqual(detail["documents"], ["rfc9110"])

    def test_a_retired_subject_leaves_the_vocabulary(self):
        self.subject.retire()
        listing = self.client.get("/api/reef/subjects/").json()
        self.assertNotIn("security", [s["slug"] for s in listing])

    def test_a_retired_subject_resolves_to_where_it_went(self):
        """Enough to redirect a link naming it, and deliberately not enough to
        render as though it were current."""
        merge_subjects(self.subject, self.target)
        detail = self.client.get("/api/reef/subjects/security/").json()
        self.assertEqual(
            detail,
            {
                "slug": "security",
                "retired": True,
                "merged_into": "security-and-privacy",
            },
        )

    def test_a_retired_subject_with_nowhere_to_go_says_so(self):
        self.subject.retire()
        detail = self.client.get("/api/reef/subjects/security/").json()
        self.assertIsNone(detail["merged_into"])

    def test_a_retired_subject_cannot_be_newly_subscribed_to(self):
        user = User.objects.create(username="u", oidc_sub="s")
        self.client.force_authenticate(user=user)
        self.subject.retire()
        response = self.client.post(
            "/api/reef/subscriptions/",
            {"kind": "subject", "subject": self.subject.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
