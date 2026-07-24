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
]
