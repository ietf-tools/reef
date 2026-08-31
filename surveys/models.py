# Copyright The IETF Trust 2026, All Rights Reserved
from django.conf import settings
from django.db import models

from .audience import normalize_audience, validate_audience


class SurveyQuerySet(models.QuerySet):
    def offerable_to(self, user):
        """Published surveys that may be offered to the given user.

        Anonymous users see open surveys only; authenticated users also see
        authenticated-visibility surveys.

        A survey the caller has already answered is left out, because Red renders
        these as toasts and a toast for a survey somebody finished last week is worse
        than no toast: it arrives unprompted and there is nothing they can do about
        it. Only Reef can know this. The visitor submits on Reef's runner, on a
        different origin from Red, so no cookie, no localStorage and no redirect tells
        Red that it happened.

        Only for an identified caller. An anonymous response records no submitter, on
        purpose, so there is deliberately nothing here to match on; suppressing those
        would mean recognising an anonymous respondent, which is a worse trade than an
        occasional repeat. Red covers that case from its own side by not re-offering a
        toast the reader has dismissed or clicked through.

        Audience targeting is not applied here: it decides where a survey is shown,
        not who may take it, and Red does that with the documents each row carries.
        """
        qs = self.filter(status=Survey.Status.PUBLISHED)
        if not (user and user.is_authenticated):
            return qs.filter(visibility=Survey.Visibility.OPEN)
        # Excluded by subquery rather than by a join, which with several responses to
        # one survey would have to be deduplicated to mean the same thing.
        return qs.exclude(
            pk__in=Response.objects.filter(submitted_by=user).values("survey")
        )


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

    def save(self, *args, **kwargs):
        # Every write path, not just the builder: the field is free-form JSON that
        # staff type, and a misspelt key would target nothing while looking targeted.
        validate_audience(self.audience)
        self.audience = normalize_audience(self.audience)
        super().save(*args, **kwargs)

    def requires_authentication(self) -> bool:
        return self.visibility == self.Visibility.AUTHENTICATED


# Pinned for SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"]. VisibilityEnum is the
# name Red and the Nuxt client already generate from, and without the override
# it would be renamed to a hash of the choices the next time another serialized
# field named visibility appeared.
SURVEY_VISIBILITY_CHOICES = Survey.Visibility.choices


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
