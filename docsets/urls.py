# Copyright The IETF Trust 2026, All Rights Reserved
from django.urls import path

from . import api

urlpatterns = [
    path("sets/", api.DocumentSetListCreate.as_view(), name="documentset-list"),
    path("sets/<uuid:pk>/", api.DocumentSetDetail.as_view(), name="documentset-detail"),
    # Before the public <slug:slug> route, which would otherwise swallow "order".
    path(
        "sets/<uuid:pk>/order/",
        api.DocumentSetOrder.as_view(),
        name="documentset-order",
    ),
    path(
        "sets/<uuid:pk>/documents/<str:doc>/",
        api.DocumentSetDocument.as_view(),
        name="documentset-document",
    ),
    path(
        "sets/<uuid:pk>/<slug:slug>/",
        api.PublicDocumentSetDetail.as_view(),
        name="documentset-public",
    ),
]
