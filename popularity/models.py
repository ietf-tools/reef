# Copyright The IETF Trust 2026, All Rights Reserved
from django.db import models

from reef.docids import DOC_ID_MAX_LENGTH, normalize_doc_id


class PopularEntry(models.Model):
    """One RFC in the curated "most popular" list.

    Per ticket #1 this is a short, manually managed list (reviewed periodically
    from analytics), maintained here via the admin and served read-only to Red.
    """

    rfc = models.CharField(max_length=DOC_ID_MAX_LENGTH, unique=True)
    rank = models.PositiveIntegerField(default=0, help_text="Lower sorts first")

    class Meta:
        ordering = ["rank"]
        verbose_name_plural = "popular entries"

    def __str__(self):
        return f"{self.rank}: {self.rfc}"

    def save(self, *args, **kwargs):
        # Canonical form, so the curated list can be joined to the same
        # documents' ratings, sets and subscriptions. The list is of RFCs, so a
        # bare number typed into the admin reads as one.
        self.rfc = normalize_doc_id(self.rfc, default_series="rfc")
        super().save(*args, **kwargs)
