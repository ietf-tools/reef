# Copyright The IETF Trust 2026, All Rights Reserved
from django.conf import settings
from django.db import models


class Subscription(models.Model):
    """A user's subscription to RFC-series notifications.

    Scaffold: tied to an authenticated user. Email delivery and datatracker
    change ingestion are stubbed (see tasks.py) pending the full build.
    """

    class Kind(models.TextChoices):
        NEW_RFC = "new_rfc", "Any new RFC"
        BY_STATUS = "by_status", "New RFC by status"
        OBSOLETED = "obsoleted", "RFC obsoleted or made historic"
        SUBJECT_TAG = "subject_tag", "RFC with a subject tag"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    params = models.JSONField(default=dict, blank=True)
    verified = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user_id}: {self.kind}"
