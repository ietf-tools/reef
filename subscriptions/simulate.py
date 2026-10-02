# Copyright The IETF Trust 2026, All Rights Reserved
"""What the next change-detection run would do with an index somebody supplies.

For the admin page that debugs missing notifications. It runs the real comparison and
matching (changes.compare, matching.plan_rfc_notifications) against the live database
and a supplied rfc-index.json, and reports the events that would be generated.

It must never change anything but its own SimulationRun row, because it can be used
against production. Three independent things enforce that, any one of which would
be enough:

- the pipeline it runs is the pure half: nothing in compare or plan_rfc_notifications
  writes, and the supplied index is parsed with rfcmeta's pure functions so it never
  reaches the shared cache, the process memo or the abstract store;
- every phase that reads the database does so inside read_only_database(), which
  refuses any statement that is not a read, and on PostgreSQL asks the server to
  refuse writes too;
- read_only_database() rolls back whatever happened regardless.

The run row's progress is written by the caller between phases, never inside one,
so those writes are outside the read-only blocks rather than exceptions to them.
"""

import json
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.db import connection, transaction

from reef import rfcmeta

from .changes import compare, decode_snapshot
from .matching import plan_rfc_notifications
from .models import DocumentSnapshot

logger = logging.getLogger("reef")

# Savepoints are what nested atomic blocks issue, so they have to get through.
_READS = ("SELECT", "SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")

# What read_only_database() itself issues, which is not work the phase asked for and
# would make every phase that reads look several queries dearer than it is.
_BOOKKEEPING = ("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT", "SET LOCAL")


def _refuse_writes(execute, sql, params, many, context):
    if not str(sql).lstrip().upper().startswith(_READS):
        raise PermissionError(f"The simulation tried to run: {str(sql)[:120]}")
    return execute(sql, params, many, context)


@contextmanager
def read_only_database():
    """Run the body so that it can read the database and cannot change it.

    The transaction is rolled back on the way out even if nothing wrote, so a missed
    case in the statement filter still leaves no trace.
    """
    connection.ensure_connection()
    with transaction.atomic():
        try:
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    # LOCAL rather than SET TRANSACTION, which is refused once the
                    # transaction has run a query -- as it will have when this is
                    # nested inside an outer block.
                    cursor.execute("SET LOCAL transaction_read_only = on")
            with connection.execute_wrapper(_refuse_writes):
                yield
        finally:
            transaction.set_rollback(True)


class _Rehearsal(logging.LoggerAdapter):
    """compare's own log lines, marked so that a simulation is not read as a run."""

    def process(self, msg, kwargs):
        return f"simulate: {msg}", kwargs


_log = _Rehearsal(logger, {})


class Phases:
    """How long each phase of a simulation took and how many queries it ran.

    on_progress is told each phase as it starts, before the phase opens any
    read-only block, so it is free to write. A phase that raises is still recorded,
    so a failed run shows how far it got.
    """

    def __init__(self, on_progress=None):
        self.on_progress = on_progress or (lambda message: None)
        self.timings = []

    @contextmanager
    def phase(self, name, message):
        self.on_progress(message)
        queries = 0

        def count(execute, sql, params, many, context):
            nonlocal queries
            if not str(sql).lstrip().upper().startswith(_BOOKKEEPING):
                queries += 1
            return execute(sql, params, many, context)

        started = time.monotonic()
        try:
            with connection.execute_wrapper(count):
                yield
        finally:
            seconds = time.monotonic() - started
            self.timings.append(
                {"phase": name, "seconds": round(seconds, 3), "queries": queries}
            )
            logger.info("simulate: %s took %.2f s (%s queries)", name, seconds, queries)


@dataclass
class ChangeRow:
    doc_display: str
    url: str
    is_new: bool
    text: str
    fields: list
    matched: dict


@dataclass
class ReaderRow:
    user: str
    email: str
    delivery: str
    events: int
    subscriptions: int


@dataclass
class Report:
    problem: str = ""
    # For an upload applied to Red's live index: the documents it added, and those
    # whose live entry it replaced.
    added: list = field(default_factory=list)
    replaced: list = field(default_factory=list)
    index_created_on: object = None
    index_count: int = 0
    snapshot_state: str = ""
    snapshot_created_on: object = None
    snapshot_count: int = 0
    outcome: str = ""
    changes_found: int = 0
    rows: list = field(default_factory=list)
    readers: list = field(default_factory=list)


# Readers are named in a run's stored report only when they are IETF staff.
# Anyone else is recorded by user id: the report is kept for debugging, and a name
# or address in it would be one more copy of somebody's personal data.
STAFF_EMAIL_DOMAIN = "@staff.ietf.org"


def identify(user):
    """(name, email) to record for a user: theirs for staff, an id for anyone else."""
    if user.email.lower().endswith(STAFF_EMAIL_DOMAIN):
        return user.username, user.email
    return f"user #{user.pk}", ""


def _delivery(user):
    if not user.receive_digest_email:
        return "web feed only: digest email is off"
    if not user.email:
        return "web feed only: no email address"
    return "web feed and email"


def uploaded_entries(upload):
    """The entries an upload to apply to the live index holds, and its createdOn.

    A list of entries, or an object with an "index" list and optionally a
    "createdOn" to use in place of the live one. Raises ValueError for any other
    shape; whether the entries themselves are right is the schema's to say.
    """
    if isinstance(upload, list):
        entries, created_on = upload, None
    elif isinstance(upload, dict) and isinstance(upload.get("index"), list):
        entries, created_on = upload["index"], upload.get("createdOn")
    else:
        raise ValueError(
            'Expected a list of index entries, or an object with an "index" list'
        )
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict) or "number" not in entry:
            raise ValueError(f"Entry {position} is not an object with a number")
    return entries, created_on


def apply_upload(live, entries, created_on=None):
    """Red's live index with the uploaded entries in it, and what they did to it.

    Returns (payload, added, replaced), where added and replaced are RFC numbers. An
    entry replaces the live one with the same number and is otherwise added.
    """
    replacing = {entry["number"]: entry for entry in entries}
    replaced, merged = [], []
    for entry in live.get("index") or []:
        number = entry.get("number") if isinstance(entry, dict) else None
        if number in replacing:
            merged.append(replacing.pop(number))
            replaced.append(number)
        else:
            merged.append(entry)
    merged.extend(replacing.values())
    payload = {**live, "index": merged}
    if created_on is not None:
        payload["createdOn"] = created_on
    return payload, list(replacing), replaced


def simulate(raw, phases=None, patch=False):
    """The Report for an uploaded file, as bytes. Changes nothing.

    The upload is a whole rfc-index.json, or with `patch` entries to apply to Red's
    live index (see apply_upload). The live index is fetched here, which is what
    keeps the upload small enough to get through the proxies in front of Reef.

    An upload that is not JSON, or not an index, is a Report with `problem` set
    rather than an exception.
    """
    phases = phases or Phases()
    report = Report()

    with phases.phase("parse", "Reading the uploaded JSON"):
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            report.problem = f"Not valid JSON: {exc}"
            return report

    if patch:
        try:
            entries, created_on = uploaded_entries(payload)
        except ValueError as exc:
            report.problem = str(exc)
            return report
        with phases.phase("fetch", "Fetching Red's live index"):
            live = rfcmeta.fetch_payload()
        if not isinstance(live, dict):
            report.problem = "Could not fetch Red's live index to apply the upload to."
            return report
        # The entries on their own first, so that a mistake in one is reported at
        # its place in the upload rather than at the end of a ten-thousand-entry
        # index.
        problem = rfcmeta.validation_problem(
            {"createdOn": created_on or live.get("createdOn"), "index": entries}
        )
        if problem is not None:
            report.problem = (
                f"The upload does not match the rfc-index schema at {problem}"
            )
            return report
        payload, report.added, report.replaced = apply_upload(live, entries, created_on)

    with phases.phase("validate", "Checking the index against the rfc-index schema"):
        problem = rfcmeta.validation_problem(payload)
        if problem is not None:
            source = "Red's live index" if patch else "The upload"
            report.problem = (
                f"{source} does not match the rfc-index schema at {problem}"
            )
            return report

    with phases.phase("reduce", "Reducing the index to the watched fields"):
        mapping, created_on = rfcmeta.reduce_payload(payload)
        # The parsed index is many times the size of the reduced mapping, and
        # nothing after this needs it.
        del payload
        index = rfcmeta.DocumentIndex(mapping, created_on)
        report.index_created_on = created_on
        report.index_count = len(index)

    with (
        phases.phase("snapshot", "Reading the live snapshot"),
        read_only_database(),
    ):
        row = DocumentSnapshot.objects.filter(pk=DocumentSnapshot.SINGLETON_PK).first()
        previous = decode_snapshot(row, log=_log) if row else None
    if row is None:
        report.snapshot_state = "none"
    elif previous is None:
        report.snapshot_state = "unreadable"
    else:
        report.snapshot_state = "present"
        report.snapshot_created_on = row.created_on
        report.snapshot_count = len(previous)

    with phases.phase("compare", f"Comparing {len(index)} documents with the snapshot"):
        result = compare(index, previous, row.created_on if row else None, log=_log)
        report.outcome = result.outcome
        report.changes_found = len(result.changes)

    with (
        phases.phase(
            "match", f"Matching {len(result.changes)} change(s) against subscriptions"
        ),
        read_only_database(),
    ):
        readers, matches = plan_rfc_notifications(result)
    for change, event, subscriptions in matches:
        kinds = {}
        for subscription in subscriptions:
            kinds[subscription.kind] = kinds.get(subscription.kind, 0) + 1
        report.rows.append(
            ChangeRow(
                doc_display=change.doc_display,
                url=change.url,
                is_new=change.is_new,
                text=event["change"],
                fields=[
                    (name, before, after)
                    for name, (before, after) in change.fields.items()
                ],
                matched=kinds,
            )
        )

    with (
        phases.phase("readers", f"Looking up {len(readers)} reader(s)"),
        read_only_database(),
    ):
        users = get_user_model().objects.in_bulk(list(readers))
    for user_id, reader in readers.items():
        user = users[user_id]
        name, email = identify(user)
        report.readers.append(
            ReaderRow(
                user=name,
                email=email,
                delivery=_delivery(user),
                events=len(reader["events"]),
                subscriptions=len(reader["subscriptions"]),
            )
        )
    report.readers.sort(key=lambda row: row.user)
    return report
