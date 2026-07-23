# Copyright The IETF Trust 2026, All Rights Reserved
from django.conf import settings
from django.db import models


class SurveyQuerySet(models.QuerySet):
    def offerable_to(self, user):
        """Published surveys that may be offered to the given user.

        Anonymous users see open surveys only; authenticated users also see
        authenticated-visibility surveys. Audience targeting is a later refinement.
        """
        qs = self.filter(status=Survey.Status.PUBLISHED)
        if not (user and user.is_authenticated):
            qs = qs.filter(visibility=Survey.Visibility.OPEN)
        return qs


class Survey(models.Model):
    """A SurveyJS survey: its JSON definition, theme, and lifecycle state."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        CLOSED = "closed", "Closed"

    class Visibility(models.TextChoices):
        OPEN = "open", "Open (anonymous)"
        AUTHENTICATED = "authenticated", "Authenticated only"

    objects = SurveyQuerySet.as_manager()

    slug = models.SlugField(max_length=100, unique=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    # SurveyJS survey JSON definition and (optional) theme JSON.
    definition = models.JSONField(default=dict)
    theme = models.JSONField(null=True, blank=True)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT
    )
    visibility = models.CharField(
        max_length=16, choices=Visibility.choices, default=Visibility.OPEN
    )

    # Targeting rules for user-specific offers (for example by subscription).
    # Scaffolded for now; not yet interpreted.
    audience = models.JSONField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="surveys_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.title} ({self.slug})"

    @property
    def is_published(self) -> bool:
        return self.status == self.Status.PUBLISHED

    def requires_authentication(self) -> bool:
        return self.visibility == self.Visibility.AUTHENTICATED


class Response(models.Model):
    """A single set of answers submitted for a survey."""

    survey = models.ForeignKey(
        Survey, on_delete=models.CASCADE, related_name="responses"
    )
    data = models.JSONField(default=dict)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="survey_responses",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"Response to {self.survey.slug} at {self.submitted_at:%Y-%m-%d %H:%M}"
