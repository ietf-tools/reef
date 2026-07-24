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
        serializer.save(user=self.request.user)


class SubscriptionDetail(generics.DestroyAPIView):
    """Delete one of the current user's subscriptions."""

    serializer_class = SubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Subscription.objects.filter(user=self.request.user)
