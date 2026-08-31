# Copyright The IETF Trust 2026, All Rights Reserved
"""Working out what has changed about the RFC series since Reef last looked.

The ticket for this called it datatracker change-feed ingestion, and there is no such
feed: the datatracker publishes a document list, a document, and the subseries, and
no events endpoint or webhook to subscribe to. What Reef does instead is diff two
readings of Red's published index, which it already fetches, validates and caches for
titles, and which carries everything the six subscription kinds need.

The previous reading is a DocumentSnapshot. Comparing is the only thing done with it:
nothing here asks the snapshot what a document's status is, only whether it is the
same as it is now.
"""

import json
import logging
import zlib
from dataclasses import dataclass, field

from django.conf import settings

from reef import rfcmeta
from reef.docids import display_doc_id

from .models import DocumentSnapshot

logger = logging.getLogger("reef")

# The fields a change to which is worth telling somebody about. Title, authors,
# abstract and formats are deliberately absent: a typo correction must not mail
# everybody tracking the document, and Red corrects those in place.
WATCHED_FIELDS = ("status", "obsoleted_by", "updates", "updated_by", "subseries")


@dataclass
class DocumentChange:
    """What happened to one document since the last run.

    One per document rather than one per field, because that is how the digest reads
    it: a document that was obsoleted and made historic in the same publication is one
    line, not two.
    """

    doc: str
    # True when the document is not in the previous snapshot at all.
    is_new: bool = False
    # field name -> (previous value, current value). Empty for a new document, whose
    # only news is that it exists.
    fields: dict = field(default_factory=dict)

    @property
    def doc_display(self):
        return display_doc_id(self.doc)

    @property
    def url(self):
        """Red's canonical page for the document.

        /info/<doc>/ rather than /rfc/<doc>, which 302s to it: a notification is read
        long after it is sent and should not spend a redirect to get where it is
        going.
        """
        base = settings.REEF_RFC_SITE_URL.rstrip("/")
        return f"{base}/info/{self.doc}/"


def reduce_index(index):
    """The watched fields of every document, from a loaded index."""
    return {
        doc: {name: meta[name] for name in WATCHED_FIELDS}
        for doc, meta in index.items()
    }


def load_snapshot():
    """The previous reading, or None if there has never been one."""
    row = DocumentSnapshot.objects.filter(pk=DocumentSnapshot.SINGLETON_PK).first()
    if row is None:
        return None
    try:
        return json.loads(zlib.decompress(bytes(row.payload)))
    except (zlib.error, ValueError) as exc:
        # Treated as absent, which makes the next run a seeding run: it will send
        # nothing and write a good snapshot, which is the safe way to recover.
        logger.error("Document snapshot is unreadable (%s); treating as absent", exc)
        return None


def save_snapshot(reduced, created_on):
    """Record a reading as the one that has been notified about."""
    DocumentSnapshot.objects.update_or_create(
        pk=DocumentSnapshot.SINGLETON_PK,
        defaults={
            "payload": zlib.compress(json.dumps(reduced).encode()),
            "created_on": created_on,
        },
    )


def diff(previous, current):
    """Every document that appeared or had a watched field change.

    Documents that vanish from the index are ignored. Red does not unpublish RFCs, so
    a disappearance is a failed build or a partial fetch rather than news, and
    reporting it would turn one bad upstream run into mail nobody can act on.
    """
    changes = []
    for doc, now in current.items():
        before = previous.get(doc)
        if before is None:
            changes.append(DocumentChange(doc=doc, is_new=True))
            continue
        moved = {
            name: (before.get(name), now.get(name))
            for name in WATCHED_FIELDS
            if before.get(name) != now.get(name)
        }
        if moved:
            changes.append(DocumentChange(doc=doc, fields=moved))
    # Sorted so that a run's output, and the mail that follows it, is in a stable
    # order rather than whatever the index happened to iterate in.
    changes.sort(key=lambda change: _sort_key(change.doc))
    return changes


def _sort_key(doc):
    digits = "".join(ch for ch in doc if ch.isdigit())
    return (doc[: len(doc) - len(digits)], int(digits) if digits else 0)


@dataclass
class Detection:
    """What a run found, and what it should record once it has acted.

    `reduced` and `created_on` are carried so the caller can advance the snapshot
    without reducing the index a second time, and so that it advances to exactly the
    reading the changes were computed from rather than to whatever Red is serving by
    the time the run finishes.
    """

    changes: list
    index: object
    reduced: dict
    created_on: object

    def save(self):
        save_snapshot(self.reduced, self.created_on)


def detect():
    """Compare Red's index with the last reading, or None if Red is unavailable.

    Reports no changes in the two cases that are not news: there is no previous
    snapshot, so this is a seeding run, and Red has not republished since the last
    run, so nothing can have changed. Both still want the snapshot advanced, which
    is the caller's to do.
    """
    index = rfcmeta.get_index()
    if index is None:
        logger.error("No index from Red, so no changes can be detected this run")
        return None

    current = reduce_index(index.mapping)
    result = Detection([], index, current, index.created_on)

    previous_row = DocumentSnapshot.objects.filter(
        pk=DocumentSnapshot.SINGLETON_PK
    ).first()
    previous = load_snapshot()

    if previous is None:
        logger.warning(
            "No previous snapshot, so seeding from %s documents and notifying nobody. "
            "Expected once; if it repeats, the snapshot is not being saved.",
            len(current),
        )
        return result

    if previous_row is not None and previous_row.created_on == index.created_on:
        # Red rebuilds when RFCs are published, so an unmoved createdOn is the normal
        # quiet case rather than a fault. Worth a line, because a createdOn that never
        # moves means Red's precomputer has stopped and no mail will ever be sent.
        logger.info(
            "Red has not republished since %s, so there is nothing to compare",
            index.created_on,
        )
        return result

    result.changes = diff(previous, current)
    logger.info(
        "Red index of %s: %s document(s) changed since the snapshot of %s",
        index.created_on,
        len(result.changes),
        previous_row.created_on if previous_row else None,
    )
    return result


# How a watched field is named to a reader, for the case where nothing else
# describes what happened to it.
FIELD_NAMES = {
    "status": "status",
    "obsoleted_by": "obsoleting documents",
    "updated_by": "updating documents",
    "updates": "updated documents",
    "subseries": "subseries membership",
}


def _names_of_fields(fields):
    named = [FIELD_NAMES[name] for name in WATCHED_FIELDS if name in fields]
    if len(named) <= 1:
        return "".join(named)
    return f"{', '.join(named[:-1])} and {named[-1]}"


def _names(identifiers):
    """Canonical identifiers as prose: "RFC 7230, RFC 7231 and RFC 7232"."""
    names = [display_doc_id(identifier) for identifier in sorted(identifiers)]
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _list_pair(pair):
    """Both sides of a relation change, or None if either is not a list.

    Guarded because a scalar would not raise: set("rfc9110") is a set of characters,
    so an unlisted value would render as "Obsoleted by rfce, rfch and rfco" rather
    than failing. Reaching this needs a snapshot written before a shape change at
    Red, which the schema would stop from arriving fresh but not from being read back.
    """
    before, after = pair
    if not isinstance(before, list) or not isinstance(after, list):
        return None
    return before, after


def _added(pair):
    sides = _list_pair(pair)
    if sides is None:
        return []
    before, after = sides
    return sorted(set(after) - set(before))


def _removed(pair):
    sides = _list_pair(pair)
    if sides is None:
        return []
    before, after = sides
    return sorted(set(before) - set(after))


def render_change(change, index):
    """The sentence a digest shows for one document, composed from the diff.

    The templates were written expecting this to arrive already written, from the
    change feed that turned out not to exist. Reef writes it instead, from the fields
    that moved plus the document's current state, which is why the index is passed in:
    a status change reads better as the name Red gives it than as the slug the
    snapshot stores.

    One sentence per fact, joined, so that a document obsoleted and made historic in
    one publication reads as one line rather than two mails.
    """
    meta = (index.mapping.get(change.doc) or {}) if index is not None else {}
    parts = []

    if change.is_new:
        status = meta.get("status_name")
        parts.append(f"Published as {status}" if status else "Published")
    else:
        if "status" in change.fields:
            status = meta.get("status_name") or change.fields["status"][1]
            parts.append(f"Status changed to {status}")
        for field_name, gained_wording, lost_wording in (
            ("obsoleted_by", "Obsoleted by {}", "No longer obsoleted by {}"),
            ("updated_by", "Updated by {}", "No longer updated by {}"),
            ("updates", "Now updates {}", "No longer updates {}"),
        ):
            if field_name not in change.fields:
                continue
            pair = change.fields[field_name]
            # Losing an entry is Red correcting a mistake rather than news about the
            # document, but it is still describable, and naming what changed beats
            # the catch-all below.
            for wording, moved in (
                (gained_wording, _added(pair)),
                (lost_wording, _removed(pair)),
            ):
                if moved:
                    parts.append(wording.format(_names(f"rfc{n}" for n in moved)))
        if "subseries" in change.fields:
            gained, lost = (
                _added(change.fields["subseries"]),
                _removed(change.fields["subseries"]),
            )
            if gained:
                parts.append(f"Added to {_names(gained)}")
            if lost:
                parts.append(f"Removed from {_names(lost)}")

    if not parts:
        # Every shape above is covered, so reaching here means a watched field moved
        # in a way this does not model -- a value changing type, most likely, after a
        # change at Red. Name the fields rather than saying nothing: a reader who can
        # see which part of the record moved can go and look.
        moved = _names_of_fields(change.fields)
        parts.append(f"Record corrected: {moved}" if moved else "Record corrected")

    return "; ".join(parts) + "."


def as_event(change, index):
    """One change in the shape delivery takes: doc, doc_display, change, url.

    The shape predates the detection path and is unchanged by it, which is the point:
    send_subscription_digest and its templates were built against it and do not have
    to know that the events now come from a diff rather than a feed.
    """
    return {
        "doc": change.doc,
        "doc_display": change.doc_display,
        "change": render_change(change, index),
        "url": change.url,
    }
