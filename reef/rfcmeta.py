# Copyright The IETF Trust 2026, All Rights Reserved
"""Document metadata, read from Red's published files and never stored.

Reef holds identifiers and almost nothing else about a document, because a title
copied into a column here would drift from the one Red publishes. Reading a title is
a different act from storing one: nothing below writes to the database, and every
value is replaced wholesale on the next read.

Three files, all anonymous, all on Red's public origin:

    /api/v1/rfc-mini-index.json          the whole series

Only the index. Red also publishes rfc-common per document and info-subseries per
container, and readers for both were written and then deleted unused: the index
carries the title and the subseries membership Reef actually needs, and one shared
copy of it beats a request per document. They are worth remembering if something ever
needs a field the index does not carry, such as an abstract.

The index is validated against reef/schemas/rfc-mini-index.schema.json, generated
from Red's Zod definition and synced by hand. The asymmetry that matters is JSON
Schema's own: a field Red adds validates fine, a required field Red removes does not.
Nothing here rejects unknown keys, and it must stay that way, or every field Red adds
becomes a Reef outage.

Every entry point degrades rather than raises. Red being slow, down or malformed
means Reef renders a document without its title, which is worse than with one and far
better than a failed precomputer run: Reef's own numbers do not depend on Red.
"""

import datetime
import json
import logging
import threading
import time
import urllib.error
import urllib.request
import zlib
from functools import lru_cache
from pathlib import Path

import jsonschema
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError

from reef.docids import normalize_doc_id

logger = logging.getLogger("reef")

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "rfc-mini-index.schema.json"

INDEX_PATH = "/api/v1/rfc-mini-index.json"
CACHE_KEY = "rfcmeta.mini-index"

# How long a process reuses its own copy before going back to the shared cache. Short,
# because it exists only to keep an admin listing from paying the decode once per row;
# the shared cache is what keeps anything from going back to Red.
PROCESS_MEMO_SECONDS = 60

_memo = {"value": None, "expires": 0.0}
_memo_lock = threading.Lock()


@lru_cache(maxsize=1)
def _schema():
    return json.loads(SCHEMA_PATH.read_text())


def _fetch(path, timeout):
    """GET and parse one of Red's files, or None if anything at all goes wrong."""
    url = f"{settings.REEF_RFC_DATA_BASE_URL.rstrip('/')}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("Could not read %s: %s", url, exc)
        return None


def _related_numbers(entries):
    """A relation reduced to bare RFC numbers.

    Red gives {id, number, title} per related document. Only the number is kept:
    keeping the titles would triple the reduced index for fields that are only ever
    compared for equality, and would make an upstream title correction read as an
    obsoletion when the snapshot is diffed.
    """
    return sorted(
        item["number"] for item in (entries or []) if item.get("number") is not None
    )


def _meta_from_entry(entry):
    """One mini-index entry reduced to what Reef uses.

    Two audiences, one reduction, because both want the same fetch. title and
    subseries are what the precomputer publishes and the admin displays; status and
    the three relation fields are what change detection compares. Adding a field here
    does not add it to anything Reef publishes: the precomputer projects down to the
    fields it means to carry.

    Subseries membership is canonicalised to Reef's own identifier form, so that a
    document set holding "std97" and a title resolved from Red agree on the spelling.
    """
    subseries = []
    for item in entry.get("subseries") or []:
        try:
            subseries.append(normalize_doc_id(f"{item['type']}{item['number']}"))
        except (DjangoValidationError, KeyError, TypeError):
            # A subseries entry Reef cannot parse is dropped rather than failing the
            # document: the title is the part callers actually need.
            logger.warning(
                "Unparseable subseries on rfc%s: %r", entry.get("number"), item
            )
    status = entry.get("status") or {}
    return {
        "title": entry.get("title"),
        "subseries": subseries,
        "status": status.get("slug"),
        "status_name": status.get("name"),
        "obsoleted_by": _related_numbers(entry.get("obsoleted_by")),
        "updates": _related_numbers(entry.get("updates")),
        "updated_by": _related_numbers(entry.get("updated_by")),
    }


def _reduce(entries):
    """Every entry down to the two fields Reef publishes, keyed by identifier.

    Reducing before caching rather than after is what keeps the cached form small:
    Red's index is 6.8 MB, and this is 209 KiB compressed.
    """
    mapping = {}
    for entry in entries:
        number = entry.get("number")
        if number is None:
            continue
        mapping[f"rfc{number}"] = _meta_from_entry(entry)
    return mapping


class DocumentIndex:
    """Red's whole series, in memory for the length of one run.

    Records the identifiers it was asked for and could not find, because that is the
    signal that matters: a frozen index does no harm while no RFCs are being
    published, and starts doing harm the moment Reef holds a document Red's copy
    does not.
    """

    def __init__(self, mapping, created_on):
        self._by_doc = mapping
        self.created_on = created_on
        self.misses = set()

    def __len__(self):
        return len(self._by_doc)

    @property
    def mapping(self):
        """The reduced index itself, for a caller comparing the whole series.

        Shared with every other holder in this process, so treat it as read-only.
        """
        return self._by_doc

    @property
    def age_days(self):
        if self.created_on is None:
            return None
        return (datetime.date.today() - self.created_on).days

    def get(self, doc_id):
        """Metadata for one identifier, or None, remembering the misses."""
        meta = self._by_doc.get(doc_id)
        if meta is None:
            self.misses.add(doc_id)
        return meta

    def report(self):
        """Log what this index was, and anything it could not answer.

        Called once at the end of a run rather than per lookup, so that a thousand
        unresolved documents are one line instead of a thousand.
        """
        age = self.age_days
        logger.info(
            "Red index: %s documents, created %s (%s days old)",
            len(self),
            self.created_on,
            "unknown" if age is None else age,
        )
        limit = settings.REEF_RFC_INDEX_MAX_AGE_DAYS
        if age is not None and age > limit:
            # A backstop, not the main signal. Red rebuilds when RFCs are published,
            # and publication is bursty enough that a tight threshold would fire
            # through every ordinary quiet fortnight.
            logger.warning(
                "Red index is %s days old, over the %s day limit. Titles may be stale.",
                age,
                limit,
            )
        if self.misses:
            sample = ", ".join(sorted(self.misses)[:10])
            logger.warning(
                "%s document(s) Reef holds are not in Red's index: %s%s",
                len(self.misses),
                sample,
                "..." if len(self.misses) > 10 else "",
            )


def _fetch_and_reduce():
    """Fetch, validate and reduce Red's index. None if it cannot be used.

    Validation is of the document as a whole rather than per entry: one pass over
    roughly ten thousand entries, a couple of seconds. That cost is why the result is
    cached and shared rather than recomputed per caller.
    """
    payload = _fetch(INDEX_PATH, settings.REEF_RFC_DATA_TIMEOUT)
    if payload is None:
        return None

    try:
        jsonschema.Draft202012Validator(_schema()).validate(payload)
    except jsonschema.ValidationError as exc:
        # The shape Reef depends on has changed. Say which field and where, because
        # the fix is in Red or in the synced schema, neither of which is here.
        logger.error(
            "Red's index no longer matches reef/schemas/rfc-mini-index.schema.json "
            "at %s: %s",
            "/".join(str(p) for p in exc.absolute_path) or "(root)",
            exc.message,
        )
        return None

    created_on = None
    try:
        created_on = datetime.date.fromisoformat(payload["createdOn"])
    except (KeyError, TypeError, ValueError):
        logger.warning("Red's index has no usable createdOn; age is unknown")

    return _reduce(payload["miniIndex"]), created_on


def load_index():
    """Fetch straight from Red, ignoring every cache. None if it cannot be used."""
    fetched = _fetch_and_reduce()
    if fetched is None:
        return None
    mapping, created_on = fetched
    return DocumentIndex(mapping, created_on)


def _from_cache(fetch):
    """The shared reduced index, fetching and storing it if the cache is cold.

    Stored as compressed JSON rather than handed to the cache as a dict, for two
    reasons. Pickled it is 784 KiB against memcached's 1 MiB default item cap, and a
    store that exceeds the cap fails without saying so, which would quietly turn every
    read back into a 6.8 MB fetch. Compressed it is 209 KiB, and JSON means nothing
    unpickles whatever is in a shared cache.
    """
    blob = cache.get(CACHE_KEY)
    if blob is not None:
        try:
            stored = json.loads(zlib.decompress(blob))
            created_on = stored["created_on"]
            return (
                stored["mapping"],
                datetime.date.fromisoformat(created_on) if created_on else None,
            )
        except (zlib.error, ValueError, KeyError, TypeError):
            # A cache entry written by an older version of this code, or a corrupt
            # one. Re-fetching is cheaper than reasoning about it.
            logger.warning("Discarding an unreadable %s cache entry", CACHE_KEY)

    if not fetch:
        return None

    fetched = _fetch_and_reduce()
    if fetched is None:
        return None
    mapping, created_on = fetched
    cache.set(
        CACHE_KEY,
        zlib.compress(
            json.dumps(
                {
                    "mapping": mapping,
                    "created_on": created_on.isoformat() if created_on else None,
                }
            ).encode()
        ),
        settings.REEF_RFC_INDEX_CACHE_SECONDS,
    )
    return mapping, created_on


def _shared(fetch):
    """The reduced index, from this process if it has a recent copy.

    Three layers looks like a lot for one file, and each earns its place: the process
    memo keeps an admin listing from decoding once per row, the shared cache keeps a
    cold process from going back to Red, and Red is the source. Only the first is
    per-process, so a stale memo lasts at most PROCESS_MEMO_SECONDS.

    `fetch` is what separates the two kinds of caller. A precomputer run wants the
    series and can afford to wait for it. Anything rendering a page cannot: a 6.8 MB
    download and a couple of seconds of validation is not a thing to put in front of
    somebody opening an admin listing, and it would make every test that touches one
    reach the network. Those callers read what is already there and go without if it
    is not.
    """
    now = time.monotonic()
    with _memo_lock:
        if _memo["value"] is not None and now < _memo["expires"]:
            return _memo["value"]

    value = _from_cache(fetch)

    with _memo_lock:
        # Only a usable index is memoised. Memoising a failure would keep a transient
        # outage in front of every caller for a minute after it had cleared.
        if value is not None:
            _memo["value"] = value
            _memo["expires"] = time.monotonic() + PROCESS_MEMO_SECONDS
    return value


def get_index():
    """The shared index, as a DocumentIndex of this caller's own.

    A fresh wrapper per call over a shared mapping, so that one run's unresolved
    documents are its own and an admin page rendering a title does not accumulate
    misses into somebody else's report.
    """
    shared = _shared(fetch=True)
    if shared is None:
        return None
    mapping, created_on = shared
    return DocumentIndex(mapping, created_on)


def cached_mapping():
    """The shared index if something has already loaded it, else None. Never fetches.

    For callers rendering a page. The None case means "nobody has loaded the index",
    which a caller has to tell apart from "this document is not in it": the first is
    Reef not knowing, the second is a document that does not exist. Showing the second
    when it is really the first would report every row as a curation error.

    The precomputer warms this on every run, so in a deployment it is almost always
    warm. On a fresh environment, `manage.py precompute` fills it.
    """
    shared = _shared(fetch=False)
    return shared[0] if shared else None


def containing_subseries(doc_id):
    """The subseries a document belongs to: rfc2119 -> ["bcp14"].

    Read off the index rather than asked of Red per document, because the index
    already carries it. An entry's subseries field is exactly this, so there is
    nothing to invert and no second lookup table worth building.

    Fetches if the index is not loaded, unlike cached_mapping(). The caller is
    subscription matching, where skipping the expansion means somebody does not get
    an email they asked for, and that is worth waiting on a fetch for in a background
    task. It still returns an empty list rather than raising when Red cannot be
    reached: what a change event should do about that is a retry decision, which
    belongs with ingest and does not exist yet. See the subseries open item.
    """
    try:
        doc_id = normalize_doc_id(doc_id)
    except DjangoValidationError:
        return []
    shared = _shared(fetch=True)
    if shared is None:
        logger.warning(
            "No document index, so %s was matched without expanding subseries", doc_id
        )
        return []
    meta = shared[0].get(doc_id)
    return list(meta["subseries"]) if meta else []


def clear_cache():
    """Drop both the shared entry and this process's copy. For tests and the admin."""
    cache.delete(CACHE_KEY)
    with _memo_lock:
        _memo["value"] = None
        _memo["expires"] = 0.0
