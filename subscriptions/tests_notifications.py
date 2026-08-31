# Copyright The IETF Trust 2026, All Rights Reserved
"""Writing a digest down before queueing it, so a broker restart cannot lose it."""

import contextlib
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from subscriptions.models import PendingNotification, Subscription
from subscriptions.tasks import (
    SendEmailError,
    deliver_notification,
    notification_key,
    queue_notification,
    sweep_unsent_notifications,
)

User = get_user_model()
EVENTS = [{"doc": "rfc9110", "change": "Published", "url": "https://example.org/"}]


class QueueNotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="reader@example.org"
        )
        self.subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )

    def test_the_row_is_written_before_anything_is_queued(self):
        """A row with nothing to deliver it is recoverable; a queued task with no row
        behind it is not."""
        with mock.patch("subscriptions.tasks.deliver_notification.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                notification = queue_notification(
                    self.user.pk, [self.subscription.pk], EVENTS
                )
        self.assertIsNone(notification.sent_at)
        self.assertEqual(notification.events, EVENTS)
        delay.assert_called_once_with(notification.pk)

    def test_nothing_is_queued_if_the_write_rolls_back(self):
        """on_commit, because a task starting before its row is visible would find
        nothing and give up."""
        with mock.patch("subscriptions.tasks.deliver_notification.delay") as delay:
            with self.captureOnCommitCallbacks(execute=False):
                queue_notification(self.user.pk, [self.subscription.pk], EVENTS)
        delay.assert_not_called()


class DeliverNotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="reader@example.org"
        )
        self.subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        self.notification = PendingNotification.objects.create(
            user=self.user, subscription_ids=[self.subscription.pk], events=EVENTS
        )

    def reload(self):
        self.notification.refresh_from_db()
        return self.notification

    def test_delivering_sends_the_mail_and_clears_the_row(self):
        """This is a queue, not a log. Once the mail has gone the row has nothing
        left to guarantee, and keeping it would accumulate one per subscriber per
        day for ever."""
        deliver_notification(self.notification.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(PendingNotification.objects.count(), 0)

    def test_a_row_that_was_sent_but_not_deleted_is_not_sent_again(self):
        """The crash window between stamping and deleting. Rare, and the whole
        reason sent_at exists."""
        self.notification.sent_at = timezone.now()
        self.notification.save()
        with self.assertLogs("reef", level="INFO"):
            deliver_notification(self.notification.pk)
        self.assertEqual(mail.outbox, [])

    def test_a_second_delivery_does_not_send_twice(self):
        """Which is what makes a broker redelivery safe: the row is gone, so the
        redelivered task finds nothing to do."""
        deliver_notification(self.notification.pk)
        with self.assertLogs("reef", level="INFO"):
            deliver_notification(self.notification.pk)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_vanished_row_is_not_an_error(self):
        pk = self.notification.pk
        self.notification.delete()
        with self.assertLogs("reef", level="INFO"):
            deliver_notification(pk)
        self.assertEqual(mail.outbox, [])

    def test_the_attempt_is_counted_even_when_the_send_fails(self):
        """So a row that keeps failing is visible as one, rather than merely absent."""
        with mock.patch(
            "reef.mail.EmailMessage.send", side_effect=OSError("smtp down")
        ):
            with self.assertRaises(SendEmailError):
                deliver_notification(self.notification.pk)
        self.assertEqual(self.reload().attempts, 1)
        self.assertIsNone(self.reload().sent_at)

    def test_a_reader_who_unsubscribed_is_settled_rather_than_retried(self):
        """Permanently nothing to send, so the obligation is discharged. Leaving the
        row would have the sweeper offer it hourly for ever."""
        self.subscription.delete()
        with self.assertLogs("reef", level="INFO"):
            deliver_notification(self.notification.pk)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(PendingNotification.objects.count(), 0)


@override_settings(
    REEF_NOTIFICATION_SWEEP_AFTER_SECONDS=3600, REEF_NOTIFICATION_MAX_ATTEMPTS=10
)
class SweepTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="reader@example.org"
        )

    def owed(self, *, age_seconds=7200, attempts=0, sent=False):
        notification = PendingNotification.objects.create(
            user=self.user,
            events=EVENTS,
            attempts=attempts,
            sent_at=timezone.now() if sent else None,
        )
        # created_at is auto_now_add, so it has to be moved afterwards.
        PendingNotification.objects.filter(pk=notification.pk).update(
            created_at=timezone.now() - datetime.timedelta(seconds=age_seconds)
        )
        return notification

    def test_an_undelivered_notification_is_put_back(self):
        """The whole reason the row exists: the broker lost it and nothing else
        would have remembered."""
        notification = self.owed()
        with mock.patch("subscriptions.tasks.deliver_notification.delay") as delay:
            self.assertEqual(sweep_unsent_notifications(), 1)
        delay.assert_called_once_with(notification.pk)

    def test_a_delivered_notification_is_left_alone(self):
        self.owed(sent=True)
        with mock.patch("subscriptions.tasks.deliver_notification.delay") as delay:
            self.assertEqual(sweep_unsent_notifications(), 0)
        delay.assert_not_called()

    def test_a_recent_notification_is_left_to_its_own_attempt(self):
        """Sweeping it now would race a delivery already in flight."""
        self.owed(age_seconds=60)
        with mock.patch("subscriptions.tasks.deliver_notification.delay"):
            self.assertEqual(sweep_unsent_notifications(), 0)

    def test_a_notification_that_has_run_out_of_attempts_is_kept_but_not_offered(self):
        """The only rows that outlive their obligation: what could never be
        delivered, kept precisely because it records that somebody did not hear
        about something."""
        self.owed(attempts=10)
        with mock.patch("subscriptions.tasks.deliver_notification.delay"):
            self.assertEqual(sweep_unsent_notifications(), 0)
        self.assertEqual(PendingNotification.objects.count(), 1)

    def test_a_sweep_that_finds_work_says_so(self):
        self.owed()
        with mock.patch("subscriptions.tasks.deliver_notification.delay"):
            with self.assertLogs("reef", level="WARNING") as logs:
                sweep_unsent_notifications()
        self.assertIn("Re-enqueued 1 undelivered", "\n".join(logs.output))

    def test_a_quiet_sweep_says_nothing(self):
        with mock.patch("subscriptions.tasks.deliver_notification.delay"):
            with self.assertNoLogs("reef", level="WARNING"):
                self.assertEqual(sweep_unsent_notifications(), 0)


@override_settings(REEF_REQUIRE_UNSUBSCRIBE_URL=True, REEF_SUBSCRIPTIONS_URL="")
class UnsubscribeUrlRequiredTests(TestCase):
    """A notification with no way to stop it does not go out.

    Reef has no unsubscribe route of its own -- Red owns the subscription UI -- so an
    unset REEF_SUBSCRIPTIONS_URL means mail carrying neither the line telling a reader
    how to stop it nor the List-Unsubscribe header.
    """

    def setUp(self):
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="reader@example.org"
        )
        self.subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.NEW_RFC
        )
        self.notification = PendingNotification.objects.create(
            user=self.user, subscription_ids=[self.subscription.pk], events=EVENTS
        )

    def test_nothing_is_sent_while_the_url_is_unset(self):
        with self.assertLogs("reef", level="ERROR"):
            deliver_notification(self.notification.pk)
        self.assertEqual(mail.outbox, [])

    def test_the_notification_is_held_rather_than_discarded(self):
        """A deployment that has not finished being configured, not a message that
        cannot be delivered."""
        with self.assertLogs("reef", level="ERROR"):
            deliver_notification(self.notification.pk)
        self.notification.refresh_from_db()
        self.assertIsNone(self.notification.sent_at)
        self.assertEqual(self.notification.attempts, 0)

    def test_it_goes_out_in_full_once_the_url_is_configured(self):
        with self.assertLogs("reef", level="ERROR"):
            deliver_notification(self.notification.pk)
        with override_settings(REEF_SUBSCRIPTIONS_URL="https://example.org/account/"):
            deliver_notification(self.notification.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("https://example.org/account/", mail.outbox[0].body)
        self.assertEqual(
            mail.outbox[0].extra_headers["List-Unsubscribe"],
            "<https://example.org/account/>",
        )
        self.assertEqual(PendingNotification.objects.count(), 0)

    @override_settings(REEF_REQUIRE_UNSUBSCRIBE_URL=False)
    def test_development_may_send_without_one(self):
        """So that mail can be exercised against mailpit without Red's page."""
        deliver_notification(self.notification.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn("List-Unsubscribe", mail.outbox[0].extra_headers)


class DedupeKeyTests(TestCase):
    """The database refuses a second copy of a notification that is still owed.

    The advisory lock stops two runs overlapping; this is the guarantee under it,
    because a lock is a convention between processes and a unique index is not.
    """

    def setUp(self):
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="reader@example.org"
        )
        self.subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.NEW_RFC
        )

    def queue(self, events=None, scope="", user=None):
        return queue_notification(
            (user or self.user).pk, [self.subscription.pk], events or EVENTS, scope
        )

    def test_queueing_the_same_thing_twice_is_refused(self):
        self.queue()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.queue()

    def test_the_refusal_leaves_one_row(self):
        self.queue()
        with contextlib.suppress(IntegrityError), transaction.atomic():
            self.queue()
        self.assertEqual(PendingNotification.objects.count(), 1)

    def test_another_reader_getting_the_same_news_is_not_a_duplicate(self):
        other = User.objects.create(username="o", oidc_sub="o", email="o@example.org")
        self.queue()
        self.queue(user=other)
        self.assertEqual(PendingNotification.objects.count(), 2)

    def test_different_news_to_one_reader_is_not_a_duplicate(self):
        self.queue()
        self.queue(events=[{"doc": "rfc8446", "change": "Published", "url": ""}])
        self.assertEqual(PendingNotification.objects.count(), 2)

    def test_a_different_scope_makes_the_same_news_new_again(self):
        """A document making the same transition again months later is a new
        notification, not one the unique index mistakes for a repeat."""
        self.queue(scope="2026-08-31")
        self.queue(scope="2026-11-02")
        self.assertEqual(PendingNotification.objects.count(), 2)

    def test_event_order_does_not_change_the_key(self):
        """Two runs finding the same changes must agree, whatever order they
        iterated in."""
        first = [
            {"doc": "rfc9110", "change": "Published", "url": ""},
            {"doc": "rfc8446", "change": "Published", "url": ""},
        ]
        self.assertEqual(
            notification_key(self.user.pk, first),
            notification_key(self.user.pk, list(reversed(first))),
        )

    def test_the_url_is_not_part_of_the_key(self):
        """It is derived from the document, so two events differing only there are
        the same news."""
        one = [{"doc": "rfc9110", "change": "Published", "url": "a"}]
        other = [{"doc": "rfc9110", "change": "Published", "url": "b"}]
        self.assertEqual(notification_key(1, one), notification_key(1, other))

    def test_a_delivered_notification_stops_blocking_its_key(self):
        """Rows are deleted on delivery, so what this forbids is a duplicate still
        owed -- which is the shape the race actually has."""
        notification = self.queue()
        deliver_notification(notification.pk)
        self.queue()  # must not raise
        self.assertEqual(PendingNotification.objects.count(), 1)
