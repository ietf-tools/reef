# Copyright The IETF Trust 2026, All Rights Reserved
"""Async notification delivery.

Two messages go to a subscriber, both rendered from templates/subscriptions/mail
and both sent on a retrying celery task. send_subscription_confirmation goes
once, when the subscription is created, and is a courtesy rather than a
verification: the subscriber authenticated through Authentik, so the address is
already known good. send_subscription_digest goes whenever a change matches.

Changes are found by diffing Red's published index, not by ingesting a feed: the
datatracker publishes no change feed, no events endpoint and no webhook, so there
was never anything to subscribe to. detect_rfc_changes runs the whole path daily,
and subscriptions/changes.py does the finding.

An event is a dict, and stays one now that it is composed here rather than arriving
from elsewhere, because delivery and its templates were built against that shape:

    doc          canonical identifier of the document that changed, per reef.docids
    doc_display  the same in prose, "RFC 9110"
    change       one line saying what happened, as the reader should see it
    url          Red's canonical page for the document

subscriptions.changes.as_event composes them from a diff.
"""

import datetime
import logging
from collections import defaultdict

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.utils import timezone

from reef import rfcmeta
from reef.docids import display_doc_id, normalize_doc_id
from reef.mail import EmailMessage
from reef.tasks import RetryTask

from .changes import _added, _removed, as_event, detect
from .models import PendingNotification, Subscription

logger = logging.getLogger("reef")

# Red's slug for a document that has been superseded or set aside.
HISTORIC_STATUS = "hist"

# Subjects name the series rather than Reef: the reader subscribed to the RFC
# series and has no reason to know which service sent the mail.
DIGEST_SUBJECT_PREFIX = "RFC series updates"

CONFIRMATION_SUBJECT = "You are now subscribed to RFC series updates"

DIGEST_TEMPLATE = "subscriptions/mail/digest.txt"
CONFIRMATION_TEMPLATE = "subscriptions/mail/confirmation.txt"


def subscriptions_for_document(doc):
    """Subscriptions that a change to one document should notify.

    Covers the three kinds that name a document. The rfc kind holds the
    identifier in params, so it is an equality test. The set kind holds a
    foreign key, so it is a join through the set's entries, against membership
    that changes underneath the subscription: someone who subscribed to a set
    last month is notified about a document added to it yesterday. The subject
    kind is the same shape of join, through the subject's assignments, and
    changes underneath the subscription the same way: subscribing to
    "security" covers whatever carries that subject when the change lands, not
    what carried it when the subscriber signed up.

    The subject kind can be matched here at all only because the vocabulary is
    Reef's own. It was drafted as a predicate over the event, alongside the
    kinds below, back when a subject was going to arrive on the event from the
    datatracker; hosting the vocabulary here turned it into a join and moved
    it off the ingest path's critical list.

    Subseries are expanded, so a change to rfc2119 matches a subscription to bcp14,
    which is what somebody subscribing to BCP 14 meant. The membership comes from
    Red's published index through reef.rfcmeta rather than from a Reef table, because
    it changes over time and Reef holds no document state to keep in step: BCP 14 is
    currently RFC 2119 and RFC 8174 and has not always been. Matching against what
    Red says today is the point, in the same way that a set subscription matches
    membership as it stands when the change lands rather than when somebody
    subscribed.

    All three kinds expand alike: a set holding bcp14 and a subject assigned to bcp14
    both match a change to rfc2119, because in each case what the subscriber named
    covers the document that changed.

    If Red cannot be reached the expansion is skipped, and a bcp14 subscriber misses
    a notification they should have had. That is a real gap rather than a tidy
    degradation; it is left here because the retry that would fix it belongs to the
    ingest path, which does not exist yet. See the subseries open item in plan.md.

    The predicate kinds (new_rfc, by_status, obsoleted) match on what happened rather
    than on which document it happened to, so they are not here; they belong to the
    ingest path once the event shape is known.
    """
    doc = normalize_doc_id(doc)
    # The changed document, plus every container it belongs to. A subscription naming
    # any of them is about this change.
    docs = [doc, *rfcmeta.containing_subseries(doc)]
    return (
        Subscription.objects.filter(
            Q(kind=Subscription.Kind.RFC, params__rfc__in=docs)
            # A set staff have taken down matches nothing. The join reaches the
            # rows directly and so does not go through DocumentSet's manager,
            # which is what would otherwise have excluded them; a real delete
            # would have taken the subscription with it.
            | Q(
                kind=Subscription.Kind.SET,
                document_set__entries__doc__in=docs,
                document_set__deleted_at__isnull=True,
            )
            # No equivalent takedown filter for subjects: a subject is staff's
            # own and has no state between existing and not, so there is no
            # row here that a read has to pretend is absent.
            | Q(kind=Subscription.Kind.SUBJECT, subject__assignments__doc__in=docs)
        )
        .distinct()
        .select_related("user", "document_set", "subject")
    )


class SendEmailError(Exception):
    """A message could not be handed to the mail server. Retryable."""


def subscriptions_for_change(change, index):
    """Every subscription one change should notify, across all six kinds.

    Two halves that cannot be one query. The kinds naming a document resolve by join,
    through subscriptions_for_document, which also expands the subseries containing
    it. The predicate kinds say what has to have happened rather than which document
    it happened to, so they are matched against the change itself; the model has said
    so since it was written, and this is the code it was waiting for.
    """
    matched = set(subscriptions_for_document(change.doc))

    # A subseries the document has left. Joining one is already covered, because
    # subscriptions_for_document expands against current membership and the document
    # is in it by then; leaving one is not, because by the time the run looks the
    # document is no longer a constituent and the expansion no longer reaches the
    # people following the container. Their subseries lost a document, which is news
    # about the subseries rather than about the document, and until the snapshot
    # started holding the previous membership there was no way to know it happened.
    for departed in _departed_subseries(change):
        matched |= set(subscriptions_for_document(departed))

    meta = (index.mapping.get(change.doc) or {}) if index is not None else {}
    predicates = Q(pk__in=[])  # matches nothing, so the ors below need no condition

    if change.is_new:
        predicates |= Q(kind=Subscription.Kind.NEW_RFC)
        status_name = meta.get("status_name")
        if status_name:
            # Stored stripped and lowercased by normalize_params, so compared that
            # way. The parameter is Red's status name rather than its slug, which is
            # what somebody subscribing through Red's UI would have picked.
            predicates |= Q(
                kind=Subscription.Kind.BY_STATUS,
                params__status=status_name.strip().lower(),
            )

    if _was_obsoleted(change):
        predicates |= Q(kind=Subscription.Kind.OBSOLETED)

    matched |= set(
        Subscription.objects.filter(predicates).select_related(
            "user", "document_set", "subject"
        )
    )
    return matched


def _departed_subseries(change):
    """The subseries this change took the document out of."""
    if change.is_new or "subseries" not in change.fields:
        return []
    return _removed(change.fields["subseries"])


def _was_obsoleted(change):
    """Whether a change is the document being obsoleted or made historic.

    Both, because the obsoleted kind offers them together: a document is usually made
    historic by the thing that obsoletes it, and occasionally without one.
    """
    if change.is_new:
        return False
    if "obsoleted_by" in change.fields and _added(change.fields["obsoleted_by"]):
        return True
    if "status" in change.fields:
        return change.fields["status"][1] == HISTORIC_STATUS
    return False


def digest_subject(events):
    """The subject line for a digest, composed here rather than in the template.

    Follows Purple, whose mail templates are bodies only. The documents are
    listed in the order the feed gave them rather than sorted: a subject that
    changes when the same batch is retried looks like a second notification.
    """
    docs = list(dict.fromkeys(event["doc"] for event in events if event.get("doc")))
    if len(docs) == 1:
        return f"{DIGEST_SUBJECT_PREFIX}: {display_doc_id(docs[0])}"
    if len(docs) > 1:
        return f"{DIGEST_SUBJECT_PREFIX}: {len(docs)} documents"
    # Every event was a predicate match with no document of its own.
    return DIGEST_SUBJECT_PREFIX


def _subscriber_context(subscription, **extra):
    """The context every message about a subscription needs."""
    return {
        "subscription": subscription,
        # Only the rfc kind has one, and the template only asks for it there.
        "watched_doc": display_doc_id(subscription.params.get("rfc", "")),
        "subscriptions_url": settings.REEF_SUBSCRIPTIONS_URL,
        **extra,
    }


def _reason(subscription):
    """One subscription as the digest names it: the object plus its prose document."""
    return {
        "subscription": subscription,
        # Only the rfc kind has one, and the template only asks for it there.
        "watched_doc": display_doc_id(subscription.params.get("rfc", "")),
    }


def render_digest(subscriptions, events):
    """Render the digest body for one subscriber.

    Takes the subscriptions that matched rather than one, because a digest is per
    reader: somebody following RFC 9110 directly and also holding it in a set hears
    about a change to it once, and is told both reasons why.
    """
    return render_to_string(
        DIGEST_TEMPLATE,
        context={
            "reasons": [_reason(subscription) for subscription in subscriptions],
            "subscriptions_url": settings.REEF_SUBSCRIPTIONS_URL,
            "events": [
                {
                    "doc_display": (
                        display_doc_id(event["doc"]) if event.get("doc") else ""
                    ),
                    "change": event.get("change", ""),
                    "url": event.get("url", ""),
                }
                for event in events
            ],
        },
    )


def render_confirmation(subscription):
    """Render the confirmation body for one subscription."""
    return render_to_string(
        CONFIRMATION_TEMPLATE, context=_subscriber_context(subscription)
    )


def _subscriber_to_mail(subscription_id, what):
    """The subscription to send to, or None when there is nothing to send.

    Both of the ways this returns None are permanent for the arguments given,
    so a caller that gets None must not retry. Raising instead would spend
    three days of retries rediscovering the same answer.
    """
    try:
        subscription = Subscription.objects.select_related("user", "document_set").get(
            pk=subscription_id
        )
    except Subscription.DoesNotExist:
        # Unsubscribing is a hard delete, so a subscription disappearing
        # between the enqueue and the send is ordinary, not an error.
        logger.info("%s: subscription=%s no longer exists", what, subscription_id)
        return None
    if subscription.document_set is not None and subscription.document_set.is_deleted:
        # The set was taken down between the enqueue and the send. Nothing to
        # send about: a real delete would have cascaded to this subscription,
        # and the message would name the set staff have just removed.
        logger.info(
            "%s: subscription=%s set=%s has been deleted",
            what,
            subscription_id,
            subscription.document_set_id,
        )
        return None
    if not subscription.user.email:
        logger.warning(
            "%s: subscription=%s user=%s has no email address",
            what,
            subscription_id,
            subscription.user_id,
        )
        return None
    return subscription


def _send(message, what, subscription_id, detail=""):
    """Send a built message, turning a delivery failure into a retry."""
    try:
        message.send()
    except Exception as err:
        logger.error("%s: subscription=%s send failed: %s", what, subscription_id, err)
        raise SendEmailError from err
    logger.info(
        "%s: subscription=%s %smessage-id=%s",
        what,
        subscription_id,
        f"{detail} " if detail else "",
        message.extra_headers.get("Message-ID"),
    )


def send_subscription_digest(
    user_id: int, subscription_ids: list[int], events: list[dict]
) -> None:
    """Send one notification to one subscriber covering everything that matched them.

    Per subscriber rather than per subscription, which is what closes the
    notification-volume question: a reader following RFC 9110 directly and also
    holding it in a document set was getting two mails about one change, and no care
    inside this task could have prevented it, because a task that sees one
    subscription cannot know about the other. The caller groups by reader and
    deduplicates by document before enqueuing, and passes every subscription that
    matched so the message can say why it arrived.

    A list of events rather than one for the same reason it always was: a
    subscription to a set of forty documents must not become forty emails when a
    batch is published.
    """
    what = "send_subscription_digest"
    if not events:
        logger.warning("%s: no events for user=%s", what, user_id)
        return False

    subscriptions = [
        subscription
        for subscription in Subscription.objects.filter(
            pk__in=subscription_ids, user_id=user_id
        ).select_related("user", "document_set", "subject")
        # A set taken down between the enqueue and the send is no reason to write to
        # anybody: a real delete would have cascaded to the subscription, and the
        # message would name a set staff have just removed.
        if subscription.document_set is None or not subscription.document_set.is_deleted
    ]
    if not subscriptions:
        # Unsubscribing is a hard delete, so every matching subscription vanishing
        # between the enqueue and the send is ordinary rather than an error, and
        # means the reader no longer wants this. Every way this returns early is
        # permanent for the arguments given, so the caller must not retry.
        logger.info(
            "%s: user=%s has no live subscriptions among %s",
            what,
            user_id,
            subscription_ids,
        )
        return False

    recipient = subscriptions[0].user
    if not recipient.email:
        logger.warning("%s: user=%s has no email address", what, user_id)
        return False

    _send(
        EmailMessage(
            subject=digest_subject(events),
            body=render_digest(subscriptions, events),
            to=[recipient.email],
        ),
        what,
        user_id,
        detail=f"events={len(events)} subscriptions={len(subscriptions)}",
    )
    return True


def queue_notification(user_id, subscription_ids, events):
    """Write down that a digest is owed, and enqueue it once the write has landed.

    Written first, enqueued after: a row with nothing to deliver it is recoverable by
    the sweeper, while a queued task with no row behind it is not recoverable at all.
    on_commit for the same reason -- a task that starts before its row is visible
    would find nothing and give up.
    """
    notification = PendingNotification.objects.create(
        user_id=user_id, subscription_ids=list(subscription_ids), events=list(events)
    )
    transaction.on_commit(lambda: deliver_notification.delay(notification.pk))
    return notification


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
    # Delivery raises SendEmailError, which the retry handles, so reaching here means
    # this notification is finished with either way. It went out, or there was
    # permanently nothing to send -- the reader unsubscribed, or has no address --
    # and both are settled: retrying would rediscover the same answer for three days.
    send_subscription_digest(
        notification.user_id, notification.subscription_ids, notification.events
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


@shared_task(
    base=RetryTask,
    autoretry_for=(SendEmailError,),
    # Shorter than a digest's: this message says "your subscription exists",
    # which the subscriber can already see in Red, and which stops being worth
    # saying long before three days are up.
    max_retries=4 * 24,  # every 15 minutes for a day, at the tail rate
    ignore_result=True,
)
def send_subscription_confirmation(subscription_id: int) -> None:
    """Tell a subscriber their new subscription exists.

    Enqueued by the create endpoint, and only when a subscription was actually
    created: POST is idempotent, so a second click must not send a second
    message.

    A courtesy, not a verification, and Reef has no verification anywhere because it
    needs none: the subscriber authenticated through Authentik and the address is the
    one on that account, which account.ietf.org has already verified. Reef never
    accepts an address typed into a form. Nothing waits on this message and a failure
    to send it never stops a digest.
    """
    subscription = _subscriber_to_mail(
        subscription_id, "send_subscription_confirmation"
    )
    if subscription is None:
        return
    _send(
        EmailMessage(
            subject=CONFIRMATION_SUBJECT,
            body=render_confirmation(subscription),
            to=[subscription.user.email],
        ),
        "send_subscription_confirmation",
        subscription_id,
    )


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
    result = detect()
    if result is None:
        # Red unreachable. The snapshot deliberately does not move, so the next run
        # compares against the same reading and nothing is missed.
        return 0

    # Per reader, not per subscription: somebody who follows a document directly and
    # also holds it in a set hears once. Events are keyed by document so that the two
    # ways of reaching one collapse into a single line rather than two.
    per_reader = defaultdict(lambda: {"subscriptions": set(), "events": {}})

    for change in result.changes:
        event = as_event(change, result.index)
        logger.info("Change: %s %s", change.doc_display, event["change"])
        for subscription in subscriptions_for_change(change, result.index):
            reader = per_reader[subscription.user_id]
            reader["subscriptions"].add(subscription.pk)
            reader["events"][change.doc] = event

    with transaction.atomic():
        for user_id, reader in per_reader.items():
            queue_notification(
                user_id,
                sorted(reader["subscriptions"]),
                list(reader["events"].values()),
            )
        result.save()

    logger.info(
        "%s change(s) notified to %s reader(s)", len(result.changes), len(per_reader)
    )
    return len(per_reader)
