# Copyright The IETF Trust 2026, All Rights Reserved
"""What the next change-detection run would do with an index somebody supplies.

For the admin page that debugs missing notifications. It runs the real comparison and
matching (changes.compare, matching.plan_rfc_notifications) against the live database
and a supplied rfc-index.json, and reports the events that would be generated.

It must never change anything, because it can be used against production. Three
independent things enforce that, any one of which would be enough:

- the pipeline it runs is the pure half: nothing in compare or plan_rfc_notifications
  writes, and the supplied index is parsed with rfcmeta's pure functions so it never
  reaches the shared cache, the process memo or the abstract store;
- read_only_database() refuses any statement that is not a read, and on PostgreSQL
  asks the server to refuse writes too;
- read_only_database() rolls back whatever happened regardless.
"""

import json
from contextlib import contextmanager
from dataclasses import dataclass, field, replace

from django.contrib.auth import get_user_model
from django.db import connection, transaction

from reef import rfcmeta

from .changes import compare, load_snapshot
from .matching import plan_rfc_notifications
from .models import DocumentSnapshot

# Planning costs a few queries per change, as the real run does. A supplied index
# compared with a stale snapshot can differ in thousands of documents, which is not
# something to run against a production database from a web request.
MAX_CHANGES = 500

# Savepoints are what nested atomic blocks issue, so they have to get through.
_READS = ("SELECT", "SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")


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
    index_created_on: object = None
    index_count: int = 0
    snapshot_state: str = ""
    snapshot_created_on: object = None
    snapshot_count: int = 0
    outcome: str = ""
    changes_found: int = 0
    truncated: bool = False
    rows: list = field(default_factory=list)
    readers: list = field(default_factory=list)


def read_payload(upload):
    """The JSON an uploaded file holds, or (None, why it is not usable)."""
    try:
        payload = json.load(upload)
    except (ValueError, UnicodeDecodeError) as exc:
        return None, f"Not valid JSON: {exc}"
    problem = rfcmeta.validation_problem(payload)
    if problem is not None:
        return None, f"Does not match the rfc-index schema at {problem}"
    return payload, ""


def _delivery(user):
    if not user.receive_digest_email:
        return "web feed only: digest email is off"
    if not user.email:
        return "web feed only: no email address"
    return "web feed and email"


def simulate(payload):
    """The Report for a validated rfc-index.json payload. Changes nothing."""
    mapping, created_on = rfcmeta.reduce_payload(payload)
    index = rfcmeta.DocumentIndex(mapping, created_on)
    report = Report(index_created_on=created_on, index_count=len(index))

    with read_only_database():
        row = DocumentSnapshot.objects.filter(pk=DocumentSnapshot.SINGLETON_PK).first()
        previous = load_snapshot()
        if row is None:
            report.snapshot_state = "none"
        elif previous is None:
            report.snapshot_state = "unreadable"
        else:
            report.snapshot_state = "present"
            report.snapshot_created_on = row.created_on
            report.snapshot_count = len(previous)

        result = compare(index, previous, row.created_on if row else None)
        report.outcome = result.outcome

        changes = result.changes
        report.changes_found = len(changes)
        if len(changes) > MAX_CHANGES:
            report.truncated = True
            changes = changes[:MAX_CHANGES]

        readers, matches = plan_rfc_notifications(replace(result, changes=changes))

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

        users = get_user_model().objects.in_bulk(list(readers))
        for user_id, reader in readers.items():
            user = users[user_id]
            report.readers.append(
                ReaderRow(
                    user=user.username,
                    email=user.email,
                    delivery=_delivery(user),
                    events=len(reader["events"]),
                    subscriptions=len(reader["subscriptions"]),
                )
            )
    report.readers.sort(key=lambda row: row.user)
    return report
