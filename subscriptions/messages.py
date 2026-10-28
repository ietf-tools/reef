# Copyright The IETF Trust 2026, All Rights Reserved
"""What a notification says: its subject line and its body.

Bodies are templates under templates/subscriptions/mail; subject lines are composed
here rather than in a template, following the templates/rpc/mail convention in Purple.
"""

from django.conf import settings
from django.template.loader import render_to_string

from reef import rfcmeta
from reef.docids import display_doc_id

DIGEST_TEMPLATE = "subscriptions/mail/digest.txt"

# Subjects name the series rather than Reef: the reader subscribed to the RFC
# series and has no reason to know which service sent the mail.
DIGEST_SUBJECT_PREFIX = "RFC series updates"


def _title(doc_id, mapping):
    return ((mapping or {}).get(doc_id) or {}).get("title")


def _titled(doc_id, mapping):
    """A document as a reader of the mail knows it: "RFC 9110: HTTP Semantics".

    Falls back to the bare identifier when there is no title to give, which is the
    case for a subseries and whenever the index has not been loaded: a mail without
    a title is better than one that waits on Red.
    """
    display = display_doc_id(doc_id)
    title = _title(doc_id, mapping)
    return f"{display}: {title}" if title else display


def digest_subject(events):
    """The subject line for a digest.

    The documents are listed in the order the events came rather than sorted: a
    subject that changes when the same batch is retried looks like a second
    notification.
    """
    docs = list(dict.fromkeys(event["doc"] for event in events if event.get("doc")))
    if len(docs) == 1:
        return f"{DIGEST_SUBJECT_PREFIX}: {_titled(docs[0], rfcmeta.cached_mapping())}"
    if len(docs) > 1:
        return f"{DIGEST_SUBJECT_PREFIX}: {len(docs)} documents"
    # Every event was a predicate match with no document of its own.
    return DIGEST_SUBJECT_PREFIX


def _reason(subscription, mapping):
    """One subscription as the digest names it: the object plus its prose document."""
    return {
        "subscription": subscription,
        "watched_doc": _titled(subscription.params.get("rfc", ""), mapping),
    }


def render_digest(subscriptions, events):
    """Render the digest body for one subscriber.

    Takes the subscriptions that matched rather than one, because a digest is per
    reader: somebody following RFC 9110 directly and also holding it in a set hears
    about a change to it once, and is told both reasons why.
    """
    mapping = rfcmeta.cached_mapping()
    return render_to_string(
        DIGEST_TEMPLATE,
        context={
            "reasons": [
                _reason(subscription, mapping) for subscription in subscriptions
            ],
            "subscriptions_url": settings.REEF_SUBSCRIPTIONS_URL,
            "events": [
                {
                    "doc_display": (
                        _titled(event["doc"], mapping) if event.get("doc") else ""
                    ),
                    "change": event.get("change", ""),
                    "url": event.get("url", ""),
                }
                for event in events
            ],
        },
    )
