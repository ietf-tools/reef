# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework import serializers

from .models import DocumentPopularity


class PopularityEntrySerializer(serializers.ModelSerializer):
    popularity = serializers.FloatField(
        source="score",
        min_value=0.0,
        max_value=1.0,
        help_text="0 is the least popular ranked document, 1 the most.",
    )

    class Meta:
        model = DocumentPopularity
        fields = ["rfc", "popularity"]


class PopularitySerializer(serializers.Serializer):
    """The whole ranking, most popular first, with when it was last computed."""

    computed_at = serializers.DateTimeField(
        allow_null=True,
        help_text="When the ranking was last recomputed; null while it is empty.",
    )
    entries = PopularityEntrySerializer(many=True)
