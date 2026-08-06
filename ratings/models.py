# Copyright The IETF Trust 2026, All Rights Reserved
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from reef.docids import DOC_ID_MAX_LENGTH, normalize_doc_id


class Rating(models.Model):
    """A single user's 1-5 rating of an RFC. Aggregated for public display."""

    rfc = models.CharField(max_length=DOC_ID_MAX_LENGTH, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ratings"
    )
    value = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("rfc", "user")]

    def __str__(self):
        return f"{self.rfc}: {self.value} by {self.user_id}"

    def save(self, *args, **kwargs):
        # Canonical form, so a rating can be joined to the same document's
        # subscriptions and document-set entries. Ratings are of RFCs, so a
        # bare number here reads as one.
        self.rfc = normalize_doc_id(self.rfc, default_series="rfc")
        super().save(*args, **kwargs)
