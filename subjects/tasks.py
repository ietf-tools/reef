# Copyright The IETF Trust 2026, All Rights Reserved
"""The Celery task behind the "Sync subject tags" admin button.

A plain wrapper around subjects.sync.run_sync: the task's whole job is running
it off the request thread and writing what happened onto the SubjectSyncRun
row a browser is polling, so the view that triggered it can redirect
immediately instead of blocking for however long a full sync takes -- see
plan.md for why a request-bound sync isn't safe (a real run against an empty
vocabulary already once outran the gunicorn worker timeout).

Not on precomputer's "precompute" queue, and not scheduled at all -- this is a
rare, manually-triggered action, not a repeating job, so it runs on Celery's
default queue like anything else that doesn't need its own.
"""

import dataclasses
import logging
import traceback

from celery import shared_task
from django.utils import timezone

from .models import SubjectSyncRun
from .sync import run_sync

logger = logging.getLogger("reef")


class _ProgressHandler(logging.Handler):
    """Mirrors each "subject sync: ..." log line onto a SubjectSyncRun's
    progress_message, so a browser polling that row while the task is still
    running has something better than "Running..." to show.

    A logging handler rather than threading a progress callback through every
    function in subjects/sync.py: those functions already log a line at every
    checkpoint (each fetch, every 50 subjects written, each phase), and
    `logger` here is the same "reef" logger they log through -- getLogger
    returns one shared instance per name -- so this reuses those lines
    instead of a second reporting mechanism saying the same things twice.

    Filtered by prefix rather than logger name, since everything in this app
    logs through the one "reef" logger: without it, this would also fire on
    unrelated log lines from anything else running in the same process while
    the task is in progress.
    """

    def __init__(self, run_id):
        super().__init__(level=logging.INFO)
        self.run_id = run_id

    def emit(self, record):
        message = record.getMessage()
        if not message.startswith("subject sync:"):
            return
        SubjectSyncRun.objects.filter(pk=self.run_id).update(progress_message=message)


@shared_task(ignore_result=True)
def run_subject_sync(run_id):
    try:
        run = SubjectSyncRun.objects.get(pk=run_id)
    except SubjectSyncRun.DoesNotExist:
        # The row this task was handed an id for is gone -- nothing sensible to
        # do but say so in the log, since there is no longer anywhere to write
        # a result.
        logger.error("subject sync: run %s vanished before it could start", run_id)
        return

    run.status = SubjectSyncRun.Status.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    handler = _ProgressHandler(run_id)
    logger.addHandler(handler)
    try:
        result = run_sync(confirm_large_change=run.confirm_large_change)
    except Exception:
        logger.error("subject sync: run %s failed", run_id, exc_info=True)
        run.status = SubjectSyncRun.Status.FAILED
        # The full traceback, not just str(exc): this is a staff-only admin
        # page, so there's no reason to make debugging a failure require
        # server log access when the log line right above already has it.
        run.error = traceback.format_exc()
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "finished_at"])
        return
    finally:
        logger.removeHandler(handler)

    if result.skipped:
        run.status = SubjectSyncRun.Status.SKIPPED
    elif result.validation_problems:
        run.status = SubjectSyncRun.Status.FAILED
    elif result.needs_confirmation:
        run.status = SubjectSyncRun.Status.NEEDS_CONFIRMATION
    else:
        run.status = SubjectSyncRun.Status.SUCCEEDED
    run.result = dataclasses.asdict(result)
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "result", "finished_at"])
