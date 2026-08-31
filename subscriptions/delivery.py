# Copyright The IETF Trust 2026, All Rights Reserved
"""Turning one notification into one sent message.

Split out of tasks.py because sending is not scheduling: these are called by the tasks
next door, and by nothing else. What lives here is the part that decides there is
somebody to write to, renders the message and hands it to the mail backend.

Nothing here retries. A failure is raised as SendEmailError and the calling task's
retry policy decides what to do about it, because how long to keep trying is a
property of the message rather than of sending.
"""

import logging

from reef.mail import EmailMessage

from .messages import (
    digest_subject,
    render_digest,
)
from .models import Subscription

logger = logging.getLogger("reef")


class SendEmailError(Exception):
    """A message could not be handed to the mail server. Retryable."""


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
