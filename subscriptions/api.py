# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from .models import Subscription
from .serializers import SubscriptionSerializer


class SubscriptionListCreate(generics.ListCreateAPIView):
    """List and create the current user's subscriptions."""

    serializer_class = SubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Subscription.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        # Subscribing is idempotent: a repeated POST (double click, resubmit,
        # second tab) returns the existing subscription rather than a duplicate.
        serializer.instance, _ = Subscription.objects.get_or_create(
            user=self.request.user,
            kind=serializer.validated_data["kind"],
            params=serializer.validated_data["params"],
            document_set=serializer.validated_data.get("document_set"),
        )


class SubscriptionDetail(generics.DestroyAPIView):
    """Delete one of the current user's subscriptions."""

    serializer_class = SubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Subscription.objects.filter(user=self.request.user)
