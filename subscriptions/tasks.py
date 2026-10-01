# Copyright The IETF Trust 2026, All Rights Reserved
"""The scheduled and enqueued work: finding changes, and getting mail sent.

What is here is what Celery has to find, plus the queue the tasks work through.
Everything a task uses has been split into modules beside this one, so that changing
who a change notifies, or what a message says, or how one is sent, does not mean
opening the module that schedules them:

    changes.py    what changed about the RFC series since Reef last looked
    matching.py   which subscriptions a change should notify
    messages.py   what a notification says
    delivery.py   turning one notification into one sent message

The queue stays here rather than in delivery, because a row and the task that works it
off are two halves of one mechanism: queue_notification exists to enqueue
deliver_notification, and separating them would only add an import cycle.
"""

import datetime
import hashlib
import json
import logging
from collections import defaultdict

from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from reef.locks import advisory_lock
from reef.tasks import RetryTask

from .changes import as_event, detect
from .delivery import SendEmailError, send_subscription_digest
from .matching import subscriptions_for_change
from .models import PendingNotification, SubjectNotificationEvent, WebNotification

logger = logging.getLogger("reef")

# Held for the length of a change-notification run.
LOCK_NAME = "subscriptions.detect_rfc_changes"


def notification_key(user_id, events, scope=""):
    """A fingerprint of one reader being told one thing.

    Over the events rather than the subscriptions, because what makes two mails
    duplicates is that they say the same thing to the same person; which of their
    subscriptions matched is why it reached them, not what it tells them.

    `scope` separates two occasions that would otherwise look identical. The change
    run passes the index reading it worked from, so that a document making the same
    transition again months later is a new notification rather than one the database
    refuses.
    """
    canonical = json.dumps(
        {
            "user": user_id,
            "scope": scope,
            "events": sorted(
                ((e.get("doc", ""), e.get("change", "")) for e in events),
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _receives_digest_email(user_id):
    return (
        get_user_model().objects.filter(pk=user_id, receive_digest_email=True).exists()
    )


def queue_notification(user_id, subscription_ids, events, scope="", web_events=None):
    """Write down that a digest is owed, and enqueue it once the write has landed.

    Written first, enqueued after: a row with nothing to deliver it is recoverable by
    the sweeper, while a queued task with no row behind it is not recoverable at all.
    on_commit for the same reason -- a task that starts before its row is visible
    would find nothing and give up.

    Raises IntegrityError if this reader already has this notification owed to them.
    Deliberately not swallowed: a caller queueing a duplicate is a caller that has
    lost track of what it has done, and for the change run that means the whole
    transaction rolls back, which is the right outcome for a run duplicating another.

    web_events is the subset of events (default: all of them) to surface as
    WebNotification rows, one each. Separate from events because a subject-tagging
    event already got one when it was staged (see stage_subject_event) and must not
    get a second one when the daily digest folds it back in here. Written
    unconditionally, regardless of the digest email preference below: the web feed
    has no opt-out.

    Returns None without creating a PendingNotification, and so without ever
    mailing anything, when the reader has turned digest email off. The
    WebNotification rows above are written either way.
    """
    for event in events if web_events is None else web_events:
        WebNotification.objects.create(
            user_id=user_id,
            kind="rfc_change",
            event=event,
            subscription_ids=list(subscription_ids),
        )

    if not _receives_digest_email(user_id):
        return None

    notification = PendingNotification.objects.create(
        user_id=user_id,
        subscription_ids=list(subscription_ids),
        events=list(events),
        dedupe_key=notification_key(user_id, events, scope),
    )
    transaction.on_commit(lambda: deliver_notification.delay(notification.pk))
    return notification


def stage_subject_event(user_id, subscription_ids, event_kind, event_key, event):
    """Hold a subject event for the next consolidated digest, and surface it on Red
    immediately.

    The WebNotification is created right after, inside the same atomic block the
    caller already wraps this call in: a retagging that hits the uniqueness
    constraint below raises before reaching it, so a fact already staged does not
    surface a second web notification either. Unconditional, regardless of the
    digest email preference -- only queue_notification's PendingNotification is
    gated by that, since only it is ever mailed.
    """
    staged = SubjectNotificationEvent.objects.create(
        user_id=user_id,
        subscription_ids=list(subscription_ids),
        event_kind=event_kind,
        event_key=event_key,
        event=event,
    )
    WebNotification.objects.create(
        user_id=user_id,
        kind=event_kind,
        event=event,
        subscription_ids=list(subscription_ids),
    )
    return staged


@shared_task(
    base=RetryTask,
    autoretry_for=(SendEmailError,),
    # Purple's judgement for mail, and it applies more strongly here: a
    # notification that finally goes out a week after the change is worse than
    # one that does not go out at all.
    max_retries=4 * 24 * 3,  # every 15 minutes for three days, at the tail rate
    ignore_result=True,
)
def deliver_notification(notification_id: int) -> None:
    """Send one owed digest and record that it went.

    The attempt is counted whatever happens, and the row is deleted once the
    notification is settled, so what remains in the table is exactly what is still
    owed plus what could never be delivered.

    Checked and then set, rather than claimed atomically first. Claiming would make a
    send that fails afterwards unrepeatable, and this codebase has already chosen its
    direction on that: RetryTask sets acks_late because a duplicate is a smaller harm
    than a silent drop. So a redelivery racing an in-flight send can duplicate, and
    that is the accepted trade rather than an oversight.
    """
    what = "deliver_notification"

    if settings.REEF_REQUIRE_UNSUBSCRIBE_URL and not settings.REEF_SUBSCRIPTIONS_URL:
        # Held rather than sent or discarded. Reef has no unsubscribe route of its
        # own, so with REEF_SUBSCRIPTIONS_URL unset the message would carry neither
        # the line telling a reader how to stop it nor the List-Unsubscribe header,
        # and a notification with no opt-out cannot be taken back once sent.
        #
        # Deliberately before the attempt is counted, so the row keeps its attempts
        # at zero and the sweeper goes on offering it: this is a deployment that has
        # not finished being configured, not a message that cannot be delivered, and
        # it should go out in full once somebody sets the URL.
        logger.error(
            "%s: REEF_SUBSCRIPTIONS_URL is not set, so notification=%s is held. "
            "Nothing will be sent until it is configured.",
            what,
            notification_id,
        )
        return

    notification = PendingNotification.objects.filter(pk=notification_id).first()
    if notification is None:
        logger.info("%s: notification=%s no longer exists", what, notification_id)
        return
    if notification.sent_at is not None:
        logger.info("%s: notification=%s already sent", what, notification_id)
        return

    PendingNotification.objects.filter(pk=notification_id).update(
        attempts=F("attempts") + 1
    )
    # Reaching here means the notification is settled either way: sent, or
    # permanently nothing to send. SendEmailError has already gone to the retry.
    #
    # Re-checked here rather than only at queue_notification: a reader can opt out
    # between the digest being queued and this task running, and a row queued
    # before they did so is left in place rather than deleted out from under a
    # possibly in-flight send (see queue_notification). Settled the same way as a
    # send either way, so the sweeper does not keep offering it.
    if _receives_digest_email(notification.user_id):
        send_subscription_digest(
            notification.user_id, notification.subscription_ids, notification.events
        )
    else:
        logger.info(
            "%s: notification=%s discarded, user=%s opted out of digest email",
            what,
            notification_id,
            notification.user_id,
        )

    # Stamped and then deleted, following Purple. The row exists to make sure the
    # notification is not lost before it goes out, and once it has gone there is
    # nothing left for it to guarantee; keeping it would accumulate a row per
    # subscriber per day for ever. The stamp covers the gap between the two
    # statements: a crash in there leaves a row that says it was sent, which the
    # sweeper skips and a redelivery declines, rather than one that gets sent twice.
    PendingNotification.objects.filter(pk=notification_id).update(
        sent_at=timezone.now()
    )
    PendingNotification.objects.filter(pk=notification_id).delete()


@shared_task(ignore_result=True)
def sweep_unsent_notifications() -> int:
    """Re-enqueue digests that were written down but never delivered.

    This is what the row is for. If the broker loses its queue, or a worker dies
    between the enqueue and the send, the notification is still recorded and this puts
    it back. Only rows old enough that an in-flight attempt would have finished are
    picked up, and only up to a limit of attempts, so a message that cannot be sent
    stops being retried rather than being offered for ever.
    """
    cutoff = timezone.now() - datetime.timedelta(
        seconds=settings.REEF_NOTIFICATION_SWEEP_AFTER_SECONDS
    )
    owed = PendingNotification.objects.filter(
        sent_at__isnull=True,
        created_at__lt=cutoff,
        attempts__lt=settings.REEF_NOTIFICATION_MAX_ATTEMPTS,
    ).values_list("pk", flat=True)

    ids = list(owed)
    for notification_id in ids:
        deliver_notification.delay(notification_id)
    if ids:
        logger.warning("Re-enqueued %s undelivered notification(s)", len(ids))
    return len(ids)


@shared_task(ignore_result=True)
def detect_rfc_changes() -> int:
    """Find what changed about the RFC series and tell the readers who asked.

    The whole notification path, once a day: diff Red's index, resolve each change to
    subscriptions, gather them per reader, write a notification for each, and only
    then record that the changes have been seen.

    That order is the point. The snapshot advances last, so a crash anywhere before
    it means the next run finds the same changes again: a repeat, which the
    notification rows and their sent stamps absorb, rather than a skip, which nothing
    would ever recover. Duplicates are recoverable and a missed change is not.

    Daily rather than hourly because Red rebuilds its index when RFCs are published
    and publication is bursty: over the last five years the median gap between
    publication dates was three days. A daily run gathers a burst into one reading;
    an hourly one would split it across several mails.

    Returns the number of readers written to, which is the number that matters
    operationally; the changes themselves are logged.
    """
    with advisory_lock(LOCK_NAME) as acquired:
        if not acquired:
            # Two of these at once is the one overlap that costs a reader something.
            # Both would read the snapshot before either advanced it, both would find
            # the same changes, and everybody would be written to twice. Skipping is
            # right: the run holding the lock is doing this work, and the next tick
            # finds the lock free.
            logger.info("Skipping change detection: another run holds the lock")
            return 0
        return _detect_and_notify()


def _detect_and_notify():
    result = detect()

    # Per reader, not per subscription: somebody who follows a document directly and
    # also holds it in a set hears once. RFC events are keyed by document and subject
    # events by their event identity, so distinct facts about one document remain.
    #
    # rfc_events shadows events with the RFC-change subset alone: a subject-tagging
    # event already got its own WebNotification when it was staged (see
    # stage_subject_event), and queue_notification uses this narrower dict to avoid
    # surfacing it a second time here.
    per_reader = defaultdict(
        lambda: {"subscriptions": set(), "events": {}, "rfc_events": {}}
    )

    if result is not None:
        for change in result.changes:
            event = as_event(change, result.index)
            logger.info("Change: %s %s", change.doc_display, event["change"])
            for subscription in subscriptions_for_change(change, result.index):
                reader = per_reader[subscription.user_id]
                reader["subscriptions"].add(subscription.pk)
                reader["events"][(change.doc, "rfc")] = event
                reader["rfc_events"][(change.doc, "rfc")] = event
    else:
        # Red is unreachable or went backwards. The snapshot deliberately remains
        # unchanged, so the next run compares against the same reading and misses
        # nothing.
        logger.info("RFC change detection skipped: no usable index from Red")

    subject_events = list(SubjectNotificationEvent.objects.all())
    for subject_event in subject_events:
        reader = per_reader[subject_event.user_id]
        reader["subscriptions"].update(subject_event.subscription_ids)
        event = subject_event.event
        reader["events"][(event.get("doc", ""), subject_event.event_key)] = event

    with transaction.atomic():
        # Red's created_on can remain unchanged for several days without a publish;
        # it is not the day this digest run is processing.
        scope = f"daily:{timezone.localdate()}"
        for user_id, reader in per_reader.items():
            queue_notification(
                user_id,
                sorted(reader["subscriptions"]),
                list(reader["events"].values()),
                scope=scope,
                web_events=list(reader["rfc_events"].values()),
            )
        if result is not None:
            result.save()
        SubjectNotificationEvent.objects.filter(
            pk__in=[subject_event.pk for subject_event in subject_events]
        ).delete()

    logger.info(
        "%s change(s) notified to %s reader(s) (%s subject event(s))",
        len(result.changes) if result is not None else 0,
        len(per_reader),
        len(subject_events),
    )
    return len(per_reader)
