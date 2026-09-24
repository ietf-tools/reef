# Copyright The IETF Trust 2026, All Rights Reserved
"""How popular each RFC is, derived from ranking sources and never typed in.

DocumentPopularity is a cache of popularity.compute.recompute_popularity() over the
input tables, of which MatomoRanking is the first. Both hold the least a ranking
needs -- which document and where it sits relative to the others -- and nothing that
could be traced back to a visitor: no visit counts, no URLs, no periods.
"""

from django.conf import settings
from django.db import models

from reef.docids import DOC_ID_MAX_LENGTH, normalize_doc_id


class RankedDocument(models.Model):
    """One RFC's place in a ranking: 0.0 is the least popular, 1.0 the most.

    Rows are written whole by an import or a recompute, as an upsert plus a
    delete of what the source no longer names, so created_at is when the document
    first entered the ranking and updated_at when it was last written.
    """

    rfc = models.CharField(max_length=DOC_ID_MAX_LENGTH, unique=True)
    score = models.FloatField()
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        abstract = True
        ordering = ["-score", "rfc"]

    def __str__(self):
        return f"{self.rfc}: {self.score:.3f}"

    def save(self, *args, **kwargs):
        # The list is of RFCs, so a bare number reads as one.
        self.rfc = normalize_doc_id(self.rfc, default_series="rfc")
        super().save(*args, **kwargs)


class MatomoRanking(RankedDocument):
    """The ranking uploaded from a Matomo page-URL export, as a rank percentile.

    Only ordering survives the import: the ratio of traffic between two documents
    is not recoverable from these rows, which is what makes them safe to keep.
    """

    class Meta(RankedDocument.Meta):
        verbose_name_plural = "matomo rankings"


class DocumentPopularity(RankedDocument):
    """The published ranking, combined from every source that has the document."""

    class Meta(RankedDocument.Meta):
        verbose_name_plural = "document popularities"


class MatomoImportRun(models.Model):
    """One upload of a Matomo export through the admin.

    The upload is parsed and MatomoRanking replaced in the request; what this row
    records is that parse's summary and then the publishing that follows it in
    Celery, which the admin page polls here rather than through Celery's result
    backend, as PrecomputeRun and subjects.SubjectSyncRun are polled.

    The counts are the only thing kept from the file. A row that named no RFC is
    a number here, never a label: the export holds personal data alongside the
    page URLs, and this row is read by anyone with admin access.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matomo_import_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    rows_seen = models.PositiveIntegerField(default=0)
    rfcs_ranked = models.PositiveIntegerField(default=0)
    rows_ignored = models.PositiveIntegerField(default=0)
    # The counted directories whose archived rows Matomo had folded into an
    # "Others" row, so the ranking covers only the documents it kept.
    truncated = models.JSONField(default=list, blank=True)
    progress_message = models.CharField(max_length=255, blank=True)
    output = models.TextField(blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_status_display()} ({self.created_at:%Y-%m-%d %H:%M})"

    @property
    def is_finished(self):
        return self.status not in (self.Status.PENDING, self.Status.RUNNING)
