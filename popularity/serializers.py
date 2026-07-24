# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework import serializers

from .models import PopularEntry


class PopularEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = PopularEntry
        fields = ["rfc", "rank"]
