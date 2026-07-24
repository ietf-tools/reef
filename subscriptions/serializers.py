# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework import serializers

from .models import Subscription


class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = ["id", "kind", "params", "verified", "created_at"]
        read_only_fields = ["id", "verified", "created_at"]
