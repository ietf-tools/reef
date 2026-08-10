# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework import serializers


class DocumentStatsSerializer(serializers.Serializer):
    """Public engagement numbers for one document."""

    doc = serializers.CharField()
    rating_average = serializers.FloatField(allow_null=True)
    rating_count = serializers.IntegerField()
    subscriber_count = serializers.IntegerField()
    set_count = serializers.IntegerField()
