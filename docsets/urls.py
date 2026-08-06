# Copyright The IETF Trust 2026, All Rights Reserved
from django.urls import path

from . import api

urlpatterns = [
    path("sets/", api.DocumentSetListCreate.as_view(), name="documentset-list"),
    path("sets/<int:pk>/", api.DocumentSetDetail.as_view(), name="documentset-detail"),
    # Before the public <slug:slug> route, which would otherwise swallow "order".
    path(
        "sets/<int:pk>/order/",
        api.DocumentSetOrder.as_view(),
        name="documentset-order",
    ),
    path(
        "sets/<int:pk>/documents/<str:doc>/",
        api.DocumentSetDocument.as_view(),
        name="documentset-document",
    ),
    path(
        "sets/<int:pk>/<slug:slug>/",
        api.PublicDocumentSetDetail.as_view(),
        name="documentset-public",
    ),
]
