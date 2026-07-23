# Copyright The IETF Trust 2026, All Rights Reserved
"""URL configuration for the Pink project."""

from django.contrib import admin
from django.http import HttpResponse
from django.urls import path

urlpatterns = [
    path("health/", lambda _: HttpResponse(status=204)),  # no content
    path("admin/", admin.site.urls),
]
