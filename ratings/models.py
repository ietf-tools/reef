# Copyright The IETF Trust 2026, All Rights Reserved
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Rating(models.Model):
    """A single user's 1-5 rating of an RFC. Aggregated for public display."""

    rfc = models.CharField(max_length=32, db_index=True)
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
