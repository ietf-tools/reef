# Copyright The IETF Trust 2026, All Rights Reserved
"""The worker's ``/api/v1/*`` paths, rendered on demand. Routed only in development."""

from django.urls import path

from . import on_demand

urlpatterns = [
    path("api/v1/<path:key>", on_demand.serve, name="precomputed-on-demand"),
]
