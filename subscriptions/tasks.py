# Copyright The IETF Trust 2026, All Rights Reserved
"""Async notification delivery (scaffold).

The full build wires ingestion of RFC-change events from the datatracker
(ticket #139) to matching subscriptions and sends email. For now these are
stubs that establish the interface and the celery task boundary.
"""

import logging

from celery import shared_task
from django.db.models import Q

from reef.docids import normalize_doc_id

from .models import Subscription

logger = logging.getLogger("reef")


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
            | Q(kind=Subscription.Kind.SET, document_set__entries__doc=doc)
        )
        .distinct()
        .select_related("user", "document_set")
    )


@shared_task(ignore_result=True)
def send_subscription_digest(subscription_id: int, events: list[dict]) -> None:
    """Send one notification for a matched subscription (stub).

    Takes a list of events rather than one: a subscription to a set of forty
    documents must not become forty emails when a batch is published, so
    delivery coalesces per subscriber over a window. Deduplication across a
    subscriber's overlapping subscriptions belongs to the caller, which is the
    only place that can see that a set subscription and an rfc subscription
    cover the same document.
    """
    logger.info(
        "send_subscription_digest (stub): subscription=%s events=%s",
        subscription_id,
        len(events),
    )


def ingest_rfc_change(event: dict) -> None:
    """Entry point for datatracker RFC-change events (stub).

    The full implementation resolves an event to subscriptions (through
    subscriptions_for_document for the kinds that name a document, and by
    predicate for the rest), coalesces them per subscriber, and enqueues
    send_subscription_digest. Wired to a datatracker feed later.
    """
    logger.info("ingest_rfc_change (stub): event=%s", event)
