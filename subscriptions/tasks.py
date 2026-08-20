# Copyright The IETF Trust 2026, All Rights Reserved
"""Async notification delivery.

Two messages go to a subscriber, both rendered from templates/subscriptions/mail
and both sent on a retrying celery task. send_subscription_confirmation goes
once, when the subscription is created, and is a courtesy rather than a
verification: the subscriber authenticated through Authentik, so the address is
already known good. send_subscription_digest goes whenever a change matches.

What is still scaffolded is ingestion of RFC-change events from the datatracker
(ticket #139), which is what decides which subscriptions a change matches and
coalesces them per subscriber before enqueuing a digest.

An event is a dict, because it arrives as one from the feed. Delivery reads
three keys and ignores the rest, so that the feed's shape can settle without
the template following it:

    doc     canonical identifier of the document that changed, per
            reef.docids (absent for an event that is not about one document)
    change  one line saying what happened, as the reader should see it
    url     where to read more (optional)

Nothing here invents those from datatracker's payload; mapping a feed record
to them belongs to ingest_rfc_change.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.template.loader import render_to_string

from reef.docids import display_doc_id, normalize_doc_id
from reef.mail import EmailMessage
from reef.tasks import RetryTask

from .models import Subscription

logger = logging.getLogger("reef")

# Subjects name the series rather than Reef: the reader subscribed to the RFC
# series and has no reason to know which service sent the mail.
DIGEST_SUBJECT_PREFIX = "RFC series updates"

CONFIRMATION_SUBJECT = "You are now subscribed to RFC series updates"

DIGEST_TEMPLATE = "subscriptions/mail/digest.txt"
CONFIRMATION_TEMPLATE = "subscriptions/mail/confirmation.txt"


def subscriptions_for_document(doc):
    """Subscriptions that a change to one document should notify.

    Covers the two kinds that name a document. The rfc kind holds the
    identifier in params, so it is an equality test. The set kind holds a
    foreign key, so it is a join through the set's entries, against membership
    that changes underneath the subscription: someone who subscribed to a set
    last month is notified about a document added to it yesterday.

    The predicate kinds (new_rfc, by_status, obsoleted, subject_tag) match on
    what happened rather than on which document it happened to, so they are not
    here; they belong to the ingest path once the event shape is known.

    Subseries are not expanded. A change to rfc2119 does not match a
    subscription to bcp14, even though BCP 14 currently consists of RFC 2119
    and RFC 8174, because Reef holds no document metadata and cannot know that.
    That expansion has to come from the datatracker feed. See the subseries
    open item in plan.md; it is the substantive unknown in this area.
    """
    doc = normalize_doc_id(doc)
    return (
        Subscription.objects.filter(
            Q(kind=Subscription.Kind.RFC, params__rfc=doc)
            # A set staff have taken down matches nothing. The join reaches the
            # rows directly and so does not go through DocumentSet's manager,
            # which is what would otherwise have excluded them; a real delete
            # would have taken the subscription with it.
            | Q(
                kind=Subscription.Kind.SET,
                document_set__entries__doc=doc,
                document_set__deleted_at__isnull=True,
            )
        )
        .distinct()
        .select_related("user", "document_set")
    )


class SendEmailError(Exception):
    """A message could not be handed to the mail server. Retryable."""


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


def render_digest(subscription, events):
    """Render the digest body for one subscription."""
    return render_to_string(
        DIGEST_TEMPLATE,
        context=_subscriber_context(
            subscription,
            events=[
                {
                    "doc_display": (
                        display_doc_id(event["doc"]) if event.get("doc") else ""
                    ),
                    "change": event.get("change", ""),
                    "url": event.get("url", ""),
                }
                for event in events
            ],
        ),
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


@shared_task(
    base=RetryTask,
    autoretry_for=(SendEmailError,),
    # Purple's judgement for mail, and it applies more strongly here: a
    # notification that finally goes out a week after the change is worse than
    # one that does not go out at all.
    max_retries=4 * 24 * 3,  # every 15 minutes for three days, at the tail rate
    ignore_result=True,
)
def send_subscription_digest(subscription_id: int, events: list[dict]) -> None:
    """Send one notification covering every event matched to one subscription.

    Takes a list of events rather than one: a subscription to a set of forty
    documents must not become forty emails when a batch is published, so
    delivery coalesces per subscriber over a window. Deduplication across a
    subscriber's overlapping subscriptions belongs to the caller, which is the
    only place that can see that a set subscription and an rfc subscription
    cover the same document.
    """
    if not events:
        logger.warning(
            "send_subscription_digest: no events for subscription=%s", subscription_id
        )
        return
    subscription = _subscriber_to_mail(subscription_id, "send_subscription_digest")
    if subscription is None:
        return
    _send(
        EmailMessage(
            subject=digest_subject(events),
            body=render_digest(subscription, events),
            to=[subscription.user.email],
        ),
        "send_subscription_digest",
        subscription_id,
        detail=f"events={len(events)}",
    )


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

    A courtesy, not a verification. The subscriber authenticated through
    Authentik and the address comes from that account, so there is nothing to
    prove; nothing waits on this message and a failure to send it never stops a
    digest. If subscribing by bare email is ever built, that case needs a real
    verification message and Subscription.verified starting False, which is a
    different message from this one rather than a flag on it.
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


def ingest_rfc_change(event: dict) -> None:
    """Entry point for datatracker RFC-change events (stub).

    The full implementation resolves an event to subscriptions (through
    subscriptions_for_document for the kinds that name a document, and by
    predicate for the rest), coalesces them per subscriber, and enqueues
    send_subscription_digest. Wired to a datatracker feed later.

    Three things are settled by delivery already existing and are not open to
    this function: an event carries doc, change and url (see the module
    docstring); one call to send_subscription_digest is one mail, so batching
    happens before the enqueue and not inside the task; and a subscriber
    holding two subscriptions that both match a change gets two mails unless
    this deduplicates per user per event, because the task cannot see the
    subscriber's other subscriptions. See the notification-volume and subseries
    open items in plan.md.
    """
    logger.info("ingest_rfc_change (stub): event=%s", event)
