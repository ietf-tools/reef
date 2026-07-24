# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework import serializers


class RatingWriteSerializer(serializers.Serializer):
    value = serializers.IntegerField(min_value=1, max_value=5)


class RatingAggregateSerializer(serializers.Serializer):
    rfc = serializers.CharField()
    average = serializers.FloatField(allow_null=True)
    count = serializers.IntegerField()
