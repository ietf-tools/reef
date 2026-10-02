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

from .changes import detect
from .delivery import SendEmailError, send_subscription_digest
from .matching import plan_rfc_notifications
from .models import PendingNotification, SubjectNotificationEvent, WebNotification

logger = logging.getLogger("reef")

# Held for the length of a change-notification run.
LOCK_NAME = "subscriptions.detect_rfc_changes"
# Held for the length of a digest run.
DIGEST_LOCK_NAME = "subscriptions.send_digest"


def notification_key(user_id, events, scope=""):
    """A fingerprint of one reader being told one thing.

    Over the events rather than the subscriptions, because what makes two mails
    duplicates is that they say the same thing to the same person; which of their
    subscriptions matched is why it reached them, not what it tells them.

    `scope` separates two occasions that would otherwise look identical. send_digest
    passes the day it runs, so the same events owed on two different days are two
    notifications rather than one the database refuses.
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
    lost track of what it has done, and for the digest run that means the whole
    transaction rolls back, which is the right outcome for a run duplicating another.

    web_events is the subset of events (default: all of them) to surface as
    WebNotification rows, one each. Separate from events because an event staged
    through stage_subject_event already got one then, and must not get a second
    when the digest folds it back in here. Written unconditionally, regardless of
    the digest email preference below: the web feed has no opt-out.

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
    """Hold a subject event, or an RFC change, for the next consolidated digest, and
    surface it on Red immediately.

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
    """Find what changed about the RFC series and surface it to the readers who asked.

    Diff Red's index, resolve each change to subscriptions, and stage one event per
    reader per change -- a web notification at once, and a row for the next digest
    (send_digest) -- and only then record that the changes have been seen.

    That order is the point. The snapshot advances in the same transaction as the
    staging, so a crash anywhere before it means the next run finds the same changes
    again rather than skipping them, which nothing would ever recover.

    Returns the number of readers staged for, which is the number that matters
    operationally; the changes themselves are logged.
    """
    with advisory_lock(LOCK_NAME) as acquired:
        if not acquired:
            # Two of these at once would both read the snapshot before either
            # advanced it, and stage every change twice. Skipping is right: the run
            # holding the lock is doing this work.
            logger.info("Skipping change detection: another run holds the lock")
            return 0
        return _detect_and_stage()


def rfc_change_event_key(doc, run_started):
    """Keyed on the document and the run that found the change, so every change
    before the digest is its own line: a status that flips back and forth reads in
    order and ends on where it settled."""
    return f"rfc-change:{doc}:{run_started.isoformat()}"


def _detect_and_stage():
    run_started = timezone.now()
    result = detect()
    if result is None:
        # Red is unreachable or went backwards. The snapshot deliberately remains
        # unchanged, so the next run compares against the same reading and misses
        # nothing.
        logger.info("RFC change detection skipped: no usable index from Red")
        return 0

    # Per reader and change, not per subscription: somebody who follows a document
    # directly and also holds it in a set hears once, with both reasons.
    staged = defaultdict(lambda: {"subscriptions": set(), "event": None})
    _readers, matches = plan_rfc_notifications(result)
    for change, event, subscriptions in matches:
        logger.info("Change: %s %s", change.doc_display, event["change"])
        for subscription in subscriptions:
            entry = staged[(subscription.user_id, change.doc)]
            entry["subscriptions"].add(subscription.pk)
            entry["event"] = event

    with transaction.atomic():
        for (user_id, doc), entry in staged.items():
            stage_subject_event(
                user_id,
                sorted(entry["subscriptions"]),
                "rfc_change",
                rfc_change_event_key(doc, run_started),
                entry["event"],
            )
        result.save()

    readers = {user_id for user_id, _doc in staged}
    logger.info(
        "%s change(s) notified to %s reader(s)", len(result.changes), len(readers)
    )
    return len(readers)


@shared_task(ignore_result=True)
def send_digest() -> int:
    """Queue one digest per reader holding everything staged since the last one:
    RFC changes from the detection runs, and subject events.

    Their web notifications went out when they were staged, so nothing here
    surfaces a second one. Returns the number of readers written to.
    """
    with advisory_lock(DIGEST_LOCK_NAME) as acquired:
        if not acquired:
            logger.info("Skipping the digest: another run holds the lock")
            return 0
        return _send_digest()


def _send_digest():
    # Keyed by document and event identity, so distinct facts about one document
    # remain distinct lines.
    per_reader = defaultdict(lambda: {"subscriptions": set(), "events": {}})
    staged = list(SubjectNotificationEvent.objects.all())
    for staged_event in staged:
        reader = per_reader[staged_event.user_id]
        reader["subscriptions"].update(staged_event.subscription_ids)
        event = staged_event.event
        reader["events"][(event.get("doc", ""), staged_event.event_key)] = event

    with transaction.atomic():
        scope = f"daily:{timezone.localdate()}"
        for user_id, reader in per_reader.items():
            queue_notification(
                user_id,
                sorted(reader["subscriptions"]),
                list(reader["events"].values()),
                scope=scope,
                web_events=[],
            )
        SubjectNotificationEvent.objects.filter(
            pk__in=[staged_event.pk for staged_event in staged]
        ).delete()

    logger.info(
        "Digest queued for %s reader(s) (%s staged event(s))",
        len(per_reader),
        len(staged),
    )
    return len(per_reader)
