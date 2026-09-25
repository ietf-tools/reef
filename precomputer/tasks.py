# Copyright The IETF Trust 2026, All Rights Reserved
"""Celery tasks that run the precomputer: on a schedule, after a staff edit, and
from the admin button.

Thin wrappers around `manage.py precompute`, deliberately: the command is the entry
point, and having the schedule call anything else would give two code paths to the
same work and one of them would drift. What these add is a lock, a queue and a
schedule -- and for the admin button, a PrecomputeRun row to report onto.

Two entries, on different periods, because the two halves go stale for different
reasons. Engagement files move whenever a reader rates or subscribes, which is
continuous and driven by Reef's own tables. Everything else moves when staff curate,
which the signals in signals.py already catch, or when Red publishes an RFC, which
nothing in Reef observes at all: the full run is the only thing that notices a
document Red has and Reef's last run did not.

None of these raise on a failed run. The command reports its own errors and exits
non-zero; a Celery exception on top would add a retry that recomputes the same
broken thing, and an alert for something the next scheduled run fixes by itself.
"""

import datetime
import io
import json
import logging
import re
import traceback
import urllib.error
import urllib.request

from celery import shared_task
from django.conf import settings
from django.core.management import CommandError, call_command
from django.utils import timezone

from reef.locks import advisory_lock

from .models import PendingDocumentChange, PrecomputeRun
from .progress import ProgressOutput

logger = logging.getLogger("reef")

LOCK_NAME = "precomputer.run"


def _run(*task_names, docs=None):
    """Run the precompute command under the lock, reporting rather than raising."""
    label = ", ".join(task_names) if task_names else "all tasks"
    with advisory_lock(LOCK_NAME) as acquired:
        if not acquired:
            # Not an error. A run in progress is already doing this work, and the
            # next tick will find the lock free.
            logger.info("Skipping precompute of %s: another run holds the lock", label)
            return False
        logger.info("Precomputing %s", label)
        try:
            call_command("precompute", *task_names, docs=docs)
        except Exception:
            logger.error("Precompute of %s failed", label, exc_info=True)
            return False
    return True


@shared_task(ignore_result=True)
def precompute_all():
    """Every task. The daily floor, and the only thing that sees Red's new RFCs."""
    return _run()


@shared_task(ignore_result=True)
def precompute_engagement():
    """The files that move with reader activity rather than with curation."""
    return _run("stats", "ratings")


PUSH_LOCK_NAME = "precomputer.push_document_changes"

_RFC_DOC_RE = re.compile(r"^rfc(\d+)$")


@shared_task(ignore_result=True)
def push_document_changes():
    """Push the documents readers changed: rewrite their files, then tell Red.

    Red bakes stats.json values into its own page data, so both runs are needed, in
    this order. Waits until writes have stopped for the quiet window, so a burst
    costs one run. Rows stay on failure; the scheduled runs remain the floor.
    """
    with advisory_lock(PUSH_LOCK_NAME) as acquired:
        if not acquired:
            logger.info("Skipping push of document changes: another run is on")
            return False
        return _push_document_changes()


def _push_document_changes():
    taken_at = timezone.now()
    pending = list(PendingDocumentChange.objects.order_by("first_seen"))
    if not pending:
        return False
    newest = max(row.last_seen for row in pending)
    quiet_for = datetime.timedelta(seconds=settings.REEF_DOCUMENT_CHANGE_QUIET_SECONDS)
    if taken_at - newest < quiet_for:
        logger.info(
            "Deferring push of %d document change(s): still being written to",
            len(pending),
        )
        return False

    docs = [row.doc for row in pending]
    if not _run("stats", "ratings", docs=docs):
        return False
    if not _notify_red(docs):
        return False

    # A document written to during the run keeps its row for the next tick.
    PendingDocumentChange.objects.filter(
        pk__in=[row.pk for row in pending], last_seen__lte=taken_at
    ).delete()
    logger.info("Pushed %d document change(s) to Red", len(docs))
    return True


def _notify_red(docs):
    """POST the RFC numbers among docs to Red's listener, in batches.

    True also when there is nothing to tell: Red has pages only for the rfc series,
    and without a listener configured it picks the change up on its own schedule.
    """
    url = settings.REEF_TRIGGER_RED_PRECOMPUTE_URL
    rfcs = [match.group(1) for doc in docs if (match := _RFC_DOC_RE.match(doc))]
    if not url:
        logger.info(
            "REEF_TRIGGER_RED_PRECOMPUTE_URL is not set; %d RFC(s) republished "
            "without telling Red",
            len(rfcs),
        )
        return True
    if not rfcs:
        return True
    size = settings.REEF_RED_PRECOMPUTE_BATCH_SIZE
    for start in range(0, len(rfcs), size):
        batch = rfcs[start : start + size]
        try:
            _post_json(url, {"rfcs": ",".join(batch)})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.error("Telling Red to rebuild %d RFC(s) failed: %s", len(batch), exc)
            return False
        logger.info("Asked Red to rebuild %d RFC(s)", len(batch))
    return True


def _post_json(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(
        request, timeout=settings.REEF_RFC_DATA_TIMEOUT
    ) as response:
        response.read()


@shared_task(ignore_result=True)
def precompute_curated():
    """The files a staff edit changes. Enqueued by signals.py, not scheduled.

    Cheap enough to run per edit: one render for popularity, one per subject, one
    per open survey. A burst of edits enqueues a task each, and the lock collapses
    only those that overlap a run in progress, so some of them repeat work. That is
    accepted rather than solved, because debouncing properly needs shared state
    that development does not have a backend for.
    """
    return _run("popularity", "subjects", "surveys")


@shared_task(ignore_result=True)
def precompute_from_admin(run_id):
    """Every task, for the admin button, reporting onto a PrecomputeRun row.

    Under the same lock as the scheduled runs, so a press that lands on one in
    progress is recorded as skipped rather than racing its purge. Unlike them it
    records a failure of any kind, traceback and all, since the row is the only
    place the person who pressed the button will look.
    """
    try:
        run = PrecomputeRun.objects.get(pk=run_id)
    except PrecomputeRun.DoesNotExist:
        logger.error("Precompute run %s vanished before it could start", run_id)
        return

    run.status = PrecomputeRun.Status.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    out, err = ProgressOutput(PrecomputeRun, run_id), io.StringIO()
    with advisory_lock(LOCK_NAME) as acquired:
        if not acquired:
            run.status = PrecomputeRun.Status.SKIPPED
            err.write(
                "Another precompute run is already in progress. Try again shortly."
            )
        else:
            try:
                call_command("precompute", stdout=out, stderr=err)
            except CommandError as exc:
                run.status = PrecomputeRun.Status.FAILED
                err.write(str(exc))
            except Exception:
                logger.error("Precompute run %s failed", run_id, exc_info=True)
                run.status = PrecomputeRun.Status.FAILED
                err.write(traceback.format_exc())
            else:
                run.status = PrecomputeRun.Status.SUCCEEDED

    run.output = out.getvalue()
    run.error = err.getvalue()
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "output", "error", "finished_at"])
