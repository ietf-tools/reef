# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework import serializers


class RatingWriteSerializer(serializers.Serializer):
    value = serializers.IntegerField(min_value=1, max_value=5)


class RatingAggregateSerializer(serializers.Serializer):
    rfc = serializers.CharField()
    average = serializers.FloatField(allow_null=True)
    count = serializers.IntegerField()
    # The caller's own rating, so a client can draw the stars filled in without
    # a second request. Null for an anonymous caller and for one who has not
    # rated this document; the two are indistinguishable to the client, which
    # already knows whether it sent a credential.
    your_rating = serializers.IntegerField(allow_null=True)
