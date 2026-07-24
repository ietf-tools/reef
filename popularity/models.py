# Copyright The IETF Trust 2026, All Rights Reserved
from django.db import models


class PopularEntry(models.Model):
    """One RFC in the curated "most popular" list.

    Per ticket #1 this is a short, manually managed list (reviewed periodically
    from analytics), maintained here via the admin and served read-only to Red.
    """

    rfc = models.CharField(max_length=32, unique=True)
    rank = models.PositiveIntegerField(default=0, help_text="Lower sorts first")

    class Meta:
        ordering = ["rank"]
        verbose_name_plural = "popular entries"

    def __str__(self):
        return f"{self.rank}: {self.rfc}"
