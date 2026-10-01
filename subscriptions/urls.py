# Copyright The IETF Trust 2026, All Rights Reserved
from django.urls import path

from . import api

urlpatterns = [
    path(
        "subscriptions/",
        api.SubscriptionListCreate.as_view(),
        name="subscription-list",
    ),
    path(
        "subscriptions/<int:pk>/",
        api.SubscriptionDetail.as_view(),
        name="subscription-detail",
    ),
    path(
        "notifications/",
        api.WebNotificationList.as_view(),
        name="notification-list",
    ),
    path(
        "notifications/read/",
        api.MarkAllNotificationsRead.as_view(),
        name="notification-read-all",
    ),
    path(
        "notifications/<int:pk>/read/",
        api.MarkNotificationRead.as_view(),
        name="notification-read",
    ),
    path(
        "digest-preference/",
        api.DigestPreferenceDetail.as_view(),
        name="digest-preference",
    ),
]
