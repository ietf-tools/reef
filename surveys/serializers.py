# Copyright The IETF Trust 2026, All Rights Reserved
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .audience import resolve_audience, validate_audience
from .models import Response, Survey


class SurveySerializer(serializers.ModelSerializer):
    """Full survey representation used by the management API and the builder."""

    class Meta:
        model = Survey
        fields = [
            "id",
            "slug",
            "title",
            "description",
            "definition",
            "theme",
            "status",
            "visibility",
            "audience",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_audience(self, value):
        """Reject a malformed audience here, not in Model.save().

        The model validates too, because every write path should, but the two raise
        different exceptions: DRF turns its own ValidationError into a 400 and lets
        Django's escape as a 500. Without this, the builder would get a server error
        for a typo in a field it is meant to let staff edit.
        """
        try:
            validate_audience(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return value


class OpenSurveySerializer(serializers.ModelSerializer):
    """One survey as Red offers it, which is as a toast.

    The fields line up with Red's Notification: title, description and url are shown,
    and slug is the string Red keys a dismissal on. documents is the addition that
    tells it where to offer this at all.
    """

    url = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()

    class Meta:
        model = Survey
        fields = ["id", "slug", "title", "description", "url", "documents"]

    def get_url(self, obj) -> str:
        base = getattr(settings, "REEF_SURVEY_RUNNER_BASE_URL", "") or ""
        return f"{base}/s/{obj.slug}"

    @extend_schema_field(
        serializers.ListField(child=serializers.CharField(), allow_null=True)
    )
    def get_documents(self, obj):
        """The documents this survey is offered on, or null for anywhere.

        Null and the empty list differ on purpose. Null is "not targeted"; [] is
        "targeted, but nothing matches yet", which is what a survey aimed at a subject
        with no documents assigned to it looks like, and which must show nowhere
        rather than everywhere.
        """
        return resolve_audience(obj.audience)


class SurveyDefinitionSerializer(serializers.ModelSerializer):
    """Definition and theme served to the Nuxt runner."""

    class Meta:
        model = Survey
        fields = ["slug", "title", "description", "definition", "theme", "visibility"]


class ResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Response
        fields = ["id", "data", "meta", "submitted_at"]
        read_only_fields = ["id", "submitted_at"]


class ResponseCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Response
        fields = ["data", "meta"]
