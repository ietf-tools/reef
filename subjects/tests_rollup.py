# Copyright The IETF Trust 2026, All Rights Reserved
"""Roll-up where it is visible: mail, statistics and survey audiences.

subjects/tests_tree.py covers the helper. These are the four callers that used to
ask the same question with the same one-hop join, tested through their own front
doors so that one of them quietly keeping the old join would fail here.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APITestCase

from reef.testing import stub_rfc_index
from stats.api import _subscriber_counts
from subscriptions.matching import subscriptions_for_document
from subscriptions.models import Subscription
from surveys.audience import resolve_audience

from .models import SubjectAssignment
from .tests_hierarchy import tree

User = get_user_model()

BRANCH = (
    "messaging",
    "messaging/email",
    "messaging/email/email-authentication",
    "messaging/email/email-authentication/dkim",
    "security",
)


class SubjectRollupTestCase(TestCase):
    def setUp(self):
        stub_rfc_index(self)
        self.made = tree(*BRANCH)
        # One document, filed as deep as the vocabulary goes.
        SubjectAssignment.objects.create(subject=self.made["dkim"], doc="rfc6376")
        self.user = User.objects.create(username="a", oidc_sub="a")

    def follow(self, slug, user=None):
        return Subscription.objects.create(
            user=user or self.user,
            kind=Subscription.Kind.SUBJECT,
            subject=self.made[slug],
        )


class MatchingTests(SubjectRollupTestCase):
    def matched(self, doc="rfc6376"):
        return {s.pk for s in subscriptions_for_document(doc)}

    def test_a_subscriber_to_the_leaf_matches(self):
        self.assertIn(self.follow("dkim").pk, self.matched())

    def test_a_subscriber_to_the_root_matches_a_document_four_levels_down(self):
        # The whole point. Before roll-up, following messaging matched nothing at
        # all, because messaging has no assignments of its own.
        self.assertEqual(self.made["messaging"].assignments.count(), 0)
        self.assertIn(self.follow("messaging").pk, self.matched())

    def test_every_ancestor_matches(self):
        expected = {
            self.follow(slug).pk
            for slug in ("dkim", "email-authentication", "email", "messaging")
        }
        self.assertEqual(self.matched(), expected)

    def test_a_sibling_branch_does_not_match(self):
        self.assertNotIn(self.follow("security").pk, self.matched())

    def test_a_document_nobody_covers_matches_nothing(self):
        self.follow("messaging")
        self.assertEqual(self.matched("rfc9110"), set())

    def test_one_subscription_is_returned_once_per_change(self):
        # A parent can now reach one document through several children, which is
        # the second reason distinct() is load-bearing here.
        SubjectAssignment.objects.create(subject=self.made["email"], doc="rfc6376")
        subscription = self.follow("messaging")
        self.assertEqual(
            [s.pk for s in subscriptions_for_document("rfc6376")], [subscription.pk]
        )

    def test_a_retired_ancestor_still_matches_for_its_followers(self):
        subscription = self.follow("messaging")
        self.made["messaging"].retire(subtree=True)
        self.assertIn(subscription.pk, self.matched())


class SubscriberCountTests(SubjectRollupTestCase):
    def test_the_count_agrees_with_who_would_be_mailed(self):
        # These two numbers disagreeing is the failure the shared helper exists to
        # prevent: a figure on the page that does not describe the mail that went.
        self.follow("messaging")
        counts = _subscriber_counts()
        matched = subscriptions_for_document("rfc6376")
        self.assertEqual(counts.get("rfc6376"), len({s.user_id for s in matched}))

    def test_a_follower_of_a_branch_counts_for_the_documents_beneath_it(self):
        self.follow("messaging")
        self.assertEqual(_subscriber_counts().get("rfc6376"), 1)

    def test_two_followers_in_one_branch_count_once_each(self):
        other = User.objects.create(username="b", oidc_sub="b")
        self.follow("messaging")
        self.follow("dkim", user=other)
        self.assertEqual(_subscriber_counts().get("rfc6376"), 2)

    def test_one_reader_following_two_subjects_in_a_branch_counts_once(self):
        self.follow("messaging")
        self.follow("dkim")
        self.assertEqual(_subscriber_counts().get("rfc6376"), 1)

    def test_a_subject_with_nothing_beneath_it_counts_for_nothing(self):
        self.follow("security")
        self.assertIsNone(_subscriber_counts().get("rfc6376"))


class AudienceTests(SubjectRollupTestCase):
    def test_naming_a_branch_targets_the_documents_beneath_it(self):
        self.assertEqual(resolve_audience({"subjects": ["messaging"]}), ["rfc6376"])

    def test_naming_the_leaf_targets_only_its_own(self):
        SubjectAssignment.objects.create(subject=self.made["security"], doc="rfc8446")
        self.assertEqual(resolve_audience({"subjects": ["dkim"]}), ["rfc6376"])

    def test_an_alias_of_a_branch_reaches_the_branch_subtree(self):
        # A rename must not silently empty an audience, and that has to hold for a
        # parent as well as for a leaf.
        self.made["messaging"].slug = "mail"
        self.made["messaging"].save()
        self.assertEqual(resolve_audience({"subjects": ["messaging"]}), ["rfc6376"])

    def test_a_branch_with_nothing_beneath_it_targets_nothing(self):
        self.assertEqual(resolve_audience({"subjects": ["security"]}), [])


class SubjectListApiTests(APITestCase):
    def setUp(self):
        self.made = tree(*BRANCH)
        SubjectAssignment.objects.create(subject=self.made["dkim"], doc="rfc6376")

    def rows(self):
        listed = self.client.get("/api/reef/subjects/").json()
        return {row["slug"]: row for row in listed}

    def test_the_deep_count_rolls_up_and_the_direct_one_does_not(self):
        rows = self.rows()
        self.assertEqual(rows["messaging"]["document_count"], 0)
        self.assertEqual(rows["messaging"]["document_count_deep"], 1)
        self.assertEqual(rows["dkim"]["document_count"], 1)

    def test_the_list_is_in_tree_order(self):
        slugs = [row["slug"] for row in self.client.get("/api/reef/subjects/").json()]
        self.assertEqual(
            slugs,
            ["messaging", "email", "email-authentication", "dkim", "security"],
        )

    def test_the_whole_vocabulary_does_not_cost_a_query_per_subject(self):
        # The counts come from one roll-up in the serializer context. Left per row,
        # this list would be a query for the direct count and a subtree query for
        # the deep one, on every subject.
        with self.assertNumQueries(3):
            self.client.get("/api/reef/subjects/")

    def test_the_doc_filter_returns_the_subjects_assigned_and_not_their_ancestors(self):
        # internet-layer on every IPv6 RFC is noise; a caller wanting the
        # breadcrumb reads it off path.
        rows = self.client.get("/api/reef/subjects/?doc=rfc6376").json()
        self.assertEqual([row["slug"] for row in rows], ["dkim"])
        self.assertEqual(rows[0]["path"], "messaging/email/email-authentication/dkim")
