# Copyright The IETF Trust 2026, All Rights Reserved
from django.db import transaction
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from .models import Subscription
from .serializers import SubscriptionSerializer
from .tasks import send_subscription_confirmation


class SubscriptionListCreate(generics.ListCreateAPIView):
    """List and create the current user's subscriptions."""

    serializer_class = SubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Subscription.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        # Subscribing is idempotent: a repeated POST (double click, resubmit,
        # second tab) returns the existing subscription rather than a duplicate.
        serializer.instance, created = Subscription.objects.get_or_create(
            user=self.request.user,
            kind=serializer.validated_data["kind"],
            params=serializer.validated_data["params"],
            document_set=serializer.validated_data.get("document_set"),
        )
        if created:
            # Only on a real create, so that the idempotent POST above does not
            # mail the same person twice for one subscription. on_commit rather
            # than a bare delay: the task reads the row back by id, so it must
            # not be able to run before the row is visible.
            subscription_id = serializer.instance.pk
            transaction.on_commit(
                lambda: send_subscription_confirmation.delay(subscription_id)
            )


class SubscriptionDetail(generics.DestroyAPIView):
    """Delete one of the current user's subscriptions."""

    serializer_class = SubscriptionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Subscription.objects.filter(user=self.request.user)
