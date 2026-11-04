# Copyright The IETF Trust 2026, All Rights Reserved
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from reef.docids import DOC_ID_MAX_LENGTH, normalize_doc_id


class RatingQuerySet(models.QuerySet):
    def live(self):
        return self.filter(deleted_at__isnull=True)


class LiveRatingManager(models.Manager.from_queryset(RatingQuerySet)):
    """Sees live ratings only. The default manager, so that omission is safe.

    A removed rating must not reach an aggregate, a statistic or a precomputed
    file, and the way that fails is a query that forgets to exclude it.
    """

    def get_queryset(self):
        return super().get_queryset().live()


class Rating(models.Model):
    """A single user's 1-5 rating of an RFC. Aggregated for public display."""

    objects = LiveRatingManager()
    all_objects = models.Manager.from_queryset(RatingQuerySet)()

    rfc = models.CharField(max_length=DOC_ID_MAX_LENGTH, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ratings"
    )
    value = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Set only when staff block the account and remove its content, and cleared
    # when they unblock it. A blocked account cannot call the API, so it never
    # meets the unique constraint by rating this document again while the row is
    # hidden; removing one rating of an account that can still rate would.
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Blank for a live rating. Set when staff remove the account's "
        "content: the rating then counts in no aggregate. Clear it to restore.",
    )
    deleted_reason = models.TextField(
        blank=True,
        help_text="Why the rating was removed. Never served through the API.",
    )

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
