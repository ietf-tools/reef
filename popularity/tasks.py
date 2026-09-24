# Copyright The IETF Trust 2026, All Rights Reserved
"""Publish an uploaded Matomo ranking: recompute popularity, then precompute the file.

The upload itself was parsed and MatomoRanking replaced in the admin request, so
by the time this runs the only data in flight is what the tables hold; what it
records on the run row, tracebacks included, cannot carry anything from the file.
"""

import io
import logging
import traceback

from celery import shared_task
from django.core.management import CommandError, call_command
from django.utils import timezone

from precomputer.progress import ProgressOutput
from precomputer.tasks import LOCK_NAME
from reef.locks import advisory_lock

from .compute import recompute_popularity
from .models import MatomoImportRun

logger = logging.getLogger("reef")

# How long to keep trying for the precomputer's lock before giving up on
# publishing from this run: a full precompute run is over in a few minutes.
LOCK_RETRY_SECONDS = 30
LOCK_MAX_RETRIES = 20


@shared_task(bind=True, ignore_result=True)
def publish_matomo_import(self, run_id):
    try:
        run = MatomoImportRun.objects.get(pk=run_id)
    except MatomoImportRun.DoesNotExist:
        logger.error("Matomo import run %s vanished before it could start", run_id)
        return

    if run.status == MatomoImportRun.Status.PENDING:
        run.status = MatomoImportRun.Status.RUNNING
        run.started_at = timezone.now()
        run.save(update_fields=["status", "started_at"])
        result = recompute_popularity()
        run.output = f"Recomputed popularity for {result.ranked} document(s)\n"
        run.progress_message = run.output.strip()
        run.save(update_fields=["output", "progress_message"])

    out, err = ProgressOutput(MatomoImportRun, run_id), io.StringIO()
    with advisory_lock(LOCK_NAME) as acquired:
        if not acquired:
            if self.request.retries < LOCK_MAX_RETRIES:
                run.progress_message = (
                    "Waiting for the precompute run in progress to finish"
                )
                run.save(update_fields=["progress_message"])
                raise self.retry(countdown=LOCK_RETRY_SECONDS)
            run.status = MatomoImportRun.Status.FAILED
            err.write(
                "Gave up waiting for the precompute run in progress. The ranking "
                "is updated; the next scheduled precompute run will publish it."
            )
        else:
            try:
                call_command("precompute", "popularity", stdout=out, stderr=err)
            except CommandError as exc:
                run.status = MatomoImportRun.Status.FAILED
                err.write(str(exc))
            except Exception:
                logger.error("Matomo import run %s failed", run_id, exc_info=True)
                run.status = MatomoImportRun.Status.FAILED
                err.write(traceback.format_exc())
            else:
                run.status = MatomoImportRun.Status.SUCCEEDED

    run.output += out.getvalue()
    run.error = err.getvalue()
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "output", "error", "finished_at"])
