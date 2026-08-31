# Copyright The IETF Trust 2026, All Rights Reserved
"""The whole path: a change in Red's index becomes mail to the readers who asked."""

import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from docsets.models import DocumentSet, DocumentSetEntry
from reef import rfcmeta
from reef.testing import stub_rfc_index
from subjects.models import Subject, SubjectAssignment
from subscriptions.changes import diff, load_snapshot, reduce_index
from subscriptions.models import PendingNotification, Subscription
from subscriptions.tasks import (
    detect_rfc_changes,
    subscriptions_for_change,
)

User = get_user_model()


def meta(**overrides):
    return {
        "title": "HTTP Semantics",
        "subseries": [],
        "status": "ps",
        "status_name": "proposed standard",
        "obsoleted_by": [],
        "updates": [],
        "updated_by": [],
        **overrides,
    }


class PredicateMatchingTests(TestCase):
    """The three kinds no join resolves, matched against the change itself."""

    def setUp(self):
        stub_rfc_index(self, {"rfc9110": meta()})
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="r@example.org"
        )

    def change(self, before, after, doc="rfc9110"):
        return diff(reduce_index(before), reduce_index(after))[0]

    def index(self, mapping):
        return rfcmeta.DocumentIndex(mapping, None)

    def matched(self, change, mapping=None):
        return subscriptions_for_change(
            change, self.index(mapping or {"rfc9110": meta()})
        )

    def test_a_new_document_matches_new_rfc(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.NEW_RFC
        )
        change = self.change({}, {"rfc9110": meta()})
        self.assertIn(subscription, self.matched(change))

    def test_a_changed_document_does_not_match_new_rfc(self):
        Subscription.objects.create(user=self.user, kind=Subscription.Kind.NEW_RFC)
        change = self.change({"rfc9110": meta()}, {"rfc9110": meta(status="hist")})
        self.assertEqual(self.matched(change), set())

    def test_by_status_matches_the_status_name_red_gives(self):
        """Not the slug: the parameter is what somebody would pick in Red's UI."""
        subscription = Subscription.objects.create(
            user=self.user,
            kind=Subscription.Kind.BY_STATUS,
            params={"status": "Proposed Standard"},  # normalised on save
        )
        change = self.change({}, {"rfc9110": meta()})
        self.assertIn(subscription, self.matched(change))

    def test_by_status_does_not_match_another_status(self):
        Subscription.objects.create(
            user=self.user,
            kind=Subscription.Kind.BY_STATUS,
            params={"status": "internet standard"},
        )
        change = self.change({}, {"rfc9110": meta()})
        self.assertEqual(self.matched(change), set())

    def test_being_obsoleted_matches_obsoleted(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.OBSOLETED
        )
        change = self.change(
            {"rfc9110": meta()}, {"rfc9110": meta(obsoleted_by=[9999])}
        )
        self.assertIn(subscription, self.matched(change))

    def test_being_made_historic_matches_obsoleted(self):
        """The kind offers them together: a document is usually made historic by the
        thing that obsoletes it, and occasionally without one."""
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.OBSOLETED
        )
        change = self.change({"rfc9110": meta()}, {"rfc9110": meta(status="hist")})
        self.assertIn(subscription, self.matched(change))

    def test_a_plain_status_change_does_not_match_obsoleted(self):
        Subscription.objects.create(user=self.user, kind=Subscription.Kind.OBSOLETED)
        change = self.change({"rfc9110": meta()}, {"rfc9110": meta(status="ds")})
        self.assertEqual(self.matched(change), set())


class NotifyRfcChangesTests(TestCase):
    """detect_rfc_changes end to end, from a moved index to queued notifications."""

    def setUp(self):
        stub_rfc_index(self, {"rfc9110": meta()})
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="reader@example.org"
        )

    def rewarm(self, mapping, created_on):
        rfcmeta._memo["value"] = (mapping, created_on)
        rfcmeta._memo["expires"] = float("inf")

    def seed(self):
        """A first run, so the second has something to compare against."""
        detect_rfc_changes()

    def test_a_seeding_run_notifies_nobody(self):
        Subscription.objects.create(user=self.user, kind=Subscription.Kind.NEW_RFC)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(detect_rfc_changes(), 0)
        self.assertEqual(PendingNotification.objects.count(), 0)

    def test_a_new_document_notifies_a_new_rfc_subscriber(self):
        self.seed()
        Subscription.objects.create(user=self.user, kind=Subscription.Kind.NEW_RFC)
        self.rewarm(
            {"rfc9110": meta(), "rfc9999": meta(title="New")},
            datetime.date(2026, 9, 1),
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(detect_rfc_changes(), 1)
        notification = PendingNotification.objects.get()
        self.assertEqual(notification.user, self.user)
        self.assertEqual(notification.events[0]["doc"], "rfc9999")

    def test_a_reader_matched_two_ways_gets_one_notification(self):
        """The coalescing that per-subscription delivery could never do."""
        self.seed()
        document_set = DocumentSet.objects.create(owner=self.user, title="HTTP core")
        DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")
        direct = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        through_set = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=document_set
        )
        self.rewarm({"rfc9110": meta(status="hist")}, datetime.date(2026, 9, 1))

        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(detect_rfc_changes(), 1)

        notification = PendingNotification.objects.get()
        self.assertEqual(len(notification.events), 1)
        self.assertEqual(
            sorted(notification.subscription_ids), sorted([direct.pk, through_set.pk])
        )

    def test_two_readers_get_one_notification_each(self):
        self.seed()
        other = User.objects.create(
            username="o", oidc_sub="o", email="other@example.org"
        )
        for user in (self.user, other):
            Subscription.objects.create(user=user, kind=Subscription.Kind.OBSOLETED)
        self.rewarm({"rfc9110": meta(obsoleted_by=[9999])}, datetime.date(2026, 9, 1))

        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(detect_rfc_changes(), 2)
        self.assertEqual(PendingNotification.objects.count(), 2)

    def test_two_changes_to_one_reader_are_one_notification(self):
        """A publication burst is one mail, not one per document."""
        self.seed()
        Subscription.objects.create(user=self.user, kind=Subscription.Kind.NEW_RFC)
        self.rewarm(
            {"rfc9110": meta(), "rfc9998": meta(), "rfc9999": meta()},
            datetime.date(2026, 9, 1),
        )
        with self.captureOnCommitCallbacks(execute=True):
            detect_rfc_changes()
        notification = PendingNotification.objects.get()
        self.assertEqual(len(notification.events), 2)

    def test_a_subject_carrying_a_subseries_is_reached(self):
        """Matching expands the subseries containing the changed document."""
        self.seed()
        subject = Subject.objects.create(name="Requirements", slug="requirements")
        SubjectAssignment.objects.create(subject=subject, doc="bcp14")
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SUBJECT, subject=subject
        )
        self.rewarm(
            {"rfc9110": meta(), "rfc2119": meta(subseries=["bcp14"])},
            datetime.date(2026, 9, 1),
        )
        with self.captureOnCommitCallbacks(execute=True):
            detect_rfc_changes()
        self.assertEqual(
            PendingNotification.objects.get().subscription_ids, [subscription.pk]
        )

    def test_nobody_subscribed_means_no_notifications(self):
        self.seed()
        self.rewarm({"rfc9110": meta(status="hist")}, datetime.date(2026, 9, 1))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(detect_rfc_changes(), 0)
        self.assertEqual(PendingNotification.objects.count(), 0)

    def test_the_snapshot_advances_only_after_the_rows_are_written(self):
        """A crash before that point repeats the run, which the sent stamps absorb;
        a skip is what nothing could recover."""
        self.seed()
        Subscription.objects.create(user=self.user, kind=Subscription.Kind.NEW_RFC)
        self.rewarm({"rfc9110": meta(), "rfc9999": meta()}, datetime.date(2026, 9, 1))
        with self.captureOnCommitCallbacks(execute=True):
            detect_rfc_changes()
        # Same reading again: the snapshot moved, so there is nothing left to report.
        self.rewarm({"rfc9110": meta(), "rfc9999": meta()}, datetime.date(2026, 9, 2))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(detect_rfc_changes(), 0)
        self.assertEqual(PendingNotification.objects.count(), 1)


class SubseriesMembershipTests(TestCase):
    """A subseries gaining or losing a constituent is news to whoever follows it.

    Gaining was already covered, because matching expands against current membership
    and the document is in it by the time the run looks. Losing was not: by then the
    document has gone, so the expansion no longer reaches the container's followers.
    Knowing it happened at all needs the previous membership, which is what the
    snapshot holds.
    """

    def setUp(self):
        stub_rfc_index(self, {"rfc2119": meta()})
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="r@example.org"
        )
        self.follows_bcp14 = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "bcp14"}
        )

    def change(self, before, after):
        return diff(reduce_index(before), reduce_index(after))[0]

    def index(self, mapping):
        return rfcmeta.DocumentIndex(mapping, None)

    def test_joining_a_subseries_reaches_its_followers(self):
        after = {"rfc2119": meta(subseries=["bcp14"])}
        change = self.change({"rfc2119": meta()}, after)
        rfcmeta._memo["value"] = (after, None)
        self.assertIn(
            self.follows_bcp14, subscriptions_for_change(change, self.index(after))
        )

    def test_leaving_a_subseries_reaches_its_followers(self):
        """The case that needed the snapshot: current membership no longer names
        bcp14, so nothing in the index could have found these people."""
        after = {"rfc2119": meta(subseries=[])}
        change = self.change({"rfc2119": meta(subseries=["bcp14"])}, after)
        rfcmeta._memo["value"] = (after, None)
        self.assertIn(
            self.follows_bcp14, subscriptions_for_change(change, self.index(after))
        )

    def test_a_change_that_is_not_about_membership_does_not_reach_them(self):
        after = {"rfc2119": meta(status="hist")}
        change = self.change({"rfc2119": meta()}, after)
        rfcmeta._memo["value"] = (after, None)
        self.assertNotIn(
            self.follows_bcp14, subscriptions_for_change(change, self.index(after))
        )

    def test_a_set_holding_the_departed_subseries_is_reached_too(self):
        """They follow bcp14 through the set, so the same news is theirs."""
        document_set = DocumentSet.objects.create(owner=self.user, title="Requirements")
        DocumentSetEntry.objects.create(document_set=document_set, doc="bcp14")
        through_set = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=document_set
        )
        after = {"rfc2119": meta(subseries=[])}
        change = self.change({"rfc2119": meta(subseries=["bcp14"])}, after)
        rfcmeta._memo["value"] = (after, None)
        self.assertIn(through_set, subscriptions_for_change(change, self.index(after)))

    def test_a_newly_published_document_has_departed_nothing(self):
        change = self.change({}, {"rfc2119": meta(subseries=["bcp14"])})
        self.assertEqual(change.fields, {})


class ConcurrentRunTests(TestCase):
    """Two notification runs at once would write to every subscriber twice.

    Worse than the overlap the precomputer guards against, which only wastes work:
    both runs read the snapshot before either advances it, so both find the same
    changes and both queue notifications for them.
    """

    def setUp(self):
        stub_rfc_index(self, {"rfc9110": meta()})
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="r@example.org"
        )
        Subscription.objects.create(user=self.user, kind=Subscription.Kind.NEW_RFC)

    def test_a_run_that_cannot_take_the_lock_does_nothing(self):
        with mock.patch("subscriptions.tasks.advisory_lock") as lock:
            lock.return_value.__enter__.return_value = False
            with self.assertLogs("reef", level="INFO") as logs:
                self.assertEqual(detect_rfc_changes(), 0)
        self.assertIn("another run holds the lock", "\n".join(logs.output))
        self.assertEqual(PendingNotification.objects.count(), 0)
        self.assertIsNone(load_snapshot())

    def test_the_lock_is_released_so_the_next_run_proceeds(self):
        with self.captureOnCommitCallbacks(execute=True):
            detect_rfc_changes()
        self.assertIsNotNone(load_snapshot())
