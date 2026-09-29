# Copyright The IETF Trust 2026, All Rights Reserved
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Subscription, WebNotification
from .serializers import (
    DigestPreferenceSerializer,
    SubscriptionSerializer,
    WebNotificationSerializer,
)


class OwnSubscriptionsMixin:
    """The caller's subscriptions, minus the ones that point at nothing.

    A subscription to a set staff have taken down is left out: the set 404s
    everywhere else, so listing a subscription that names it would be the one
    thing that still says it exists. Hidden rather than deleted, so restoring
    the set brings the subscription back with it.
    """

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return (
            Subscription.objects.filter(user=self.request.user)
            .filter(
                Q(document_set__isnull=True) | Q(document_set__deleted_at__isnull=True)
            )
            .select_related("subject")  # subject_details, per row
        )


class SubscriptionListCreate(OwnSubscriptionsMixin, generics.ListCreateAPIView):
    """List and create the current user's subscriptions."""

    serializer_class = SubscriptionSerializer

    def perform_create(self, serializer):
        # Subscribing is idempotent: a repeated POST (double click, resubmit,
        # second tab) returns the existing subscription rather than a duplicate.
        #
        # Every relation column, not just the ones this kind fills: a lookup
        # that left one out would match a row holding it and hand back a
        # subscription to something the caller did not ask for.
        relations = {
            field: serializer.validated_data.get(field)
            for field in Subscription.RELATIONS.values()
        }
        serializer.instance, _ = Subscription.objects.get_or_create(
            user=self.request.user,
            kind=serializer.validated_data["kind"],
            params=serializer.validated_data["params"],
            **relations,
        )


class SubscriptionDetail(OwnSubscriptionsMixin, generics.DestroyAPIView):
    """Delete one of the current user's subscriptions."""

    serializer_class = SubscriptionSerializer


class NotificationCursorPagination(CursorPagination):
    """Ordered by row rather than by created_at: two notifications written in the
    same instant would otherwise tie, and pk is monotonic without one."""

    ordering = "-pk"
    page_size = 20


class OwnWebNotificationsMixin:
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return WebNotification.objects.filter(user=self.request.user)


class WebNotificationList(OwnWebNotificationsMixin, generics.ListAPIView):
    """The caller's own notification feed, newest first."""

    serializer_class = WebNotificationSerializer
    pagination_class = NotificationCursorPagination


class MarkNotificationRead(OwnWebNotificationsMixin, APIView):
    """Mark one of the caller's own notifications read.

    Idempotent: a notification already read stays read rather than erroring.
    """

    @extend_schema(request=None, responses={200: WebNotificationSerializer})
    def post(self, request, pk):
        notification = get_object_or_404(self.get_queryset(), pk=pk)
        if not notification.read:
            notification.read = True
            notification.save(update_fields=["read"])
        return Response(WebNotificationSerializer(notification).data)


class DigestPreferenceDetail(generics.RetrieveUpdateAPIView):
    """Read or set whether the caller receives the subscription digest by mail.

    Always the caller's own account: there is nothing else this could name.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = DigestPreferenceSerializer

    def get_object(self):
        return self.request.user
