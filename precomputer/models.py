# Copyright The IETF Trust 2026, All Rights Reserved
from django.conf import settings
from django.db import models


class PrecomputeRun(models.Model):
    """One press of the admin "Run precompute now" button.

    A full run renders several hundred files and uploads each one, which takes
    long enough on a deployment that a request waiting on it shows nothing for a
    minute or more and heads for the gunicorn timeout. So the admin view creates
    one of these, hands its id to precomputer.tasks.precompute_from_admin, and
    redirects to a page that polls this row -- the same arrangement as
    subjects.SubjectSyncRun, and like it, never Celery's own result backend,
    which this project doesn't keep (CELERY_TASK_IGNORE_RESULT).

    Only admin runs are recorded. The scheduled and signal-driven runs report to
    the log, as they always have; nobody is watching a page for them.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="precompute_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # The command's latest line of output, written as the run goes: what the
    # polling page shows while it is still running.
    progress_message = models.CharField(max_length=255, blank=True)
    output = models.TextField(blank=True)
    # The command's stderr, which carries warnings on a run that succeeded as
    # well as the reason for one that failed.
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_status_display()} ({self.created_at:%Y-%m-%d %H:%M})"

    @property
    def is_finished(self):
        return self.status not in (self.Status.PENDING, self.Status.RUNNING)
