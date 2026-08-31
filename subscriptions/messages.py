# Copyright The IETF Trust 2026, All Rights Reserved
"""What a notification says: its subject line and its body.

Split out of tasks.py so that changing the wording does not mean opening the module
that schedules and delivers. Bodies are templates under templates/subscriptions/mail;
subject lines are composed here rather than in a template, following the
templates/rpc/mail convention in Purple.
"""

from django.conf import settings
from django.template.loader import render_to_string

from reef.docids import display_doc_id

DIGEST_TEMPLATE = "subscriptions/mail/digest.txt"
CONFIRMATION_TEMPLATE = "subscriptions/mail/confirmation.txt"

# Subjects name the series rather than Reef: the reader subscribed to the RFC
# series and has no reason to know which service sent the mail.
DIGEST_SUBJECT_PREFIX = "RFC series updates"
CONFIRMATION_SUBJECT = "You are now subscribed to RFC series updates"


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
