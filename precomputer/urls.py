# Copyright The IETF Trust 2026, All Rights Reserved
"""The worker's ``/api/v1/*`` paths, rendered live. Routed only in development."""

from django.urls import path

from . import live

urlpatterns = [
    path("api/v1/<path:key>", live.serve, name="precomputed-live"),
]
