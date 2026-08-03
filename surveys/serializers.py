# Copyright The IETF Trust 2026, All Rights Reserved
from django.conf import settings
from rest_framework import serializers

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


class OpenSurveySerializer(serializers.ModelSerializer):
    """Minimal representation for Red's open-survey list and popover."""

    url = serializers.SerializerMethodField()

    class Meta:
        model = Survey
        fields = ["id", "slug", "title", "description", "url"]

    def get_url(self, obj) -> str:
        base = getattr(settings, "REEF_SURVEY_RUNNER_BASE_URL", "") or ""
        return f"{base}/s/{obj.slug}"


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
