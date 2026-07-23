# Copyright The IETF Trust 2026, All Rights Reserved
"""URL configuration for the Pink project."""

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path

urlpatterns = [
    path("health/", lambda _: HttpResponse(status=204)),  # no content
    path("admin/", admin.site.urls),
]

if settings.DEBUG and "debug_toolbar" in settings.INSTALLED_APPS:
    urlpatterns = [path("__debug__/", include("debug_toolbar.urls"))] + urlpatterns
