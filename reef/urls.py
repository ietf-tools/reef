# Copyright The IETF Trust 2026, All Rights Reserved
"""URL configuration for the Reef project."""

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("health/", lambda _: HttpResponse(status=204)),  # no content
    path("admin/", admin.site.urls),
    path("oidc/", include("mozilla_django_oidc.urls")),
    path("api/reef/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/reef/schema/swagger-ui/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/reef/", include("surveys.urls")),
    path("api/reef/", include("ratings.urls")),
    path("api/reef/", include("popularity.urls")),
    path("api/reef/", include("docsets.urls")),
    path("api/reef/", include("subscriptions.urls")),
    path("api/reef/", include("stats.urls")),
    path("manage/", include("surveys.manage_urls")),
]

if settings.DEBUG and "debug_toolbar" in settings.INSTALLED_APPS:
    urlpatterns = [path("__debug__/", include("debug_toolbar.urls"))] + urlpatterns
