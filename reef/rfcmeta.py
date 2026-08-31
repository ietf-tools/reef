# Copyright The IETF Trust 2026, All Rights Reserved
"""Document metadata, read from Red's published files and never stored.

Reef holds identifiers and almost nothing else about a document, because a title
copied into a column here would drift from the one Red publishes. Reading a title is
a different act from storing one: nothing below writes to the database, and every
value is replaced wholesale on the next read.

Three files, all anonymous, all on Red's public origin:

    /api/v1/rfc-mini-index.json          the whole series, for a bulk sweep
    /api/v1/rfc-common/{number}.json     one document, fuller
    /api/v1/info-subseries/{t}{n}.json   a container and the RFCs in it

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
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

import jsonschema
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError

from reef.docids import normalize_doc_id

logger = logging.getLogger("reef")

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "rfc-mini-index.schema.json"

INDEX_PATH = "/api/v1/rfc-mini-index.json"
DOC_PATH = "/api/v1/rfc-common/{number}.json"
SUBSERIES_PATH = "/api/v1/info-subseries/{doc}.json"


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


def _meta_from_entry(entry):
    """The subset Reef publishes, from one mini-index entry.

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
    return {"title": entry.get("title"), "subseries": subseries}


class DocumentIndex:
    """Red's whole series, in memory for the length of one run.

    Records the identifiers it was asked for and could not find, because that is the
    signal that matters: a frozen index does no harm while no RFCs are being
    published, and starts doing harm the moment Reef holds a document Red's copy
    does not.
    """

    def __init__(self, entries, created_on):
        self._by_doc = {}
        for entry in entries:
            number = entry.get("number")
            if number is None:
                continue
            self._by_doc[f"rfc{number}"] = _meta_from_entry(entry)
        self.created_on = created_on
        self.misses = set()

    def __len__(self):
        return len(self._by_doc)

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


def load_index():
    """Fetch and validate the whole series. None if it cannot be used.

    Validation is of the document as a whole rather than per entry, which is one pass
    over roughly ten thousand entries and takes a couple of seconds. That is why this
    is called once per run and the result passed around, rather than per lookup.
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

    return DocumentIndex(payload["miniIndex"], created_on)


def fetch_doc(doc_id):
    """Metadata for one document, read per-document rather than from the sweep.

    For the callers that want one title and not ten thousand: an admin page, a
    confirmation email. Only RFCs have an rfc-common file, so a subseries identifier
    returns None here.
    """
    try:
        doc_id = normalize_doc_id(doc_id)
    except DjangoValidationError:
        return None
    if not doc_id.startswith("rfc"):
        return None
    payload = _fetch(
        DOC_PATH.format(number=doc_id.removeprefix("rfc")),
        settings.REEF_RFC_DATA_TIMEOUT,
    )
    if payload is None:
        return None
    return _meta_from_entry(payload)


def fetch_subseries(doc_id):
    """The canonical identifiers a subseries contains, or None.

    This is what a BCP or STD in a document set or a subscription expands to. It
    comes from Red rather than from a Reef table because subseries membership
    changes over time and Reef holds no document state to keep in step.
    """
    try:
        doc_id = normalize_doc_id(doc_id)
    except DjangoValidationError:
        return None
    if doc_id.startswith("rfc"):
        return None
    payload = _fetch(SUBSERIES_PATH.format(doc=doc_id), settings.REEF_RFC_DATA_TIMEOUT)
    if payload is None:
        return None
    contents = []
    for entry in payload.get("contents") or []:
        number = entry.get("number")
        if number is not None:
            contents.append(f"rfc{number}")
    return contents
