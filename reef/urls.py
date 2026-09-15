# Copyright The IETF Trust 2026, All Rights Reserved
"""URL configuration for the Reef project."""

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("health/", lambda _: HttpResponse(status=204)),
    # Ahead of admin.site.urls: both are under admin/, and Django tries top-level
    # patterns in order, falling through to the next on a Resolver404 from the one
    # it tried -- but listing the more specific prefix first is the clearer way to
    # keep this out of admin.site.urls's own path space.
    path("admin/survey-builder/", include("surveys.manage_urls")),
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
    path("api/reef/", include("subjects.urls")),
    path("api/reef/", include("subscriptions.urls")),
    path("api/reef/", include("stats.urls")),
    path("api/reef/", include("me.urls")),
]

if settings.DEBUG and "debug_toolbar" in settings.INSTALLED_APPS:
    urlpatterns = [path("__debug__/", include("debug_toolbar.urls"))] + urlpatterns
