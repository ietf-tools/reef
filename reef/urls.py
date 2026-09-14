# Copyright The IETF Trust 2026, All Rights Reserved
"""URL configuration for the Reef project."""

from django.conf import settings
from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from mozilla_django_oidc.views import OIDCAuthenticationRequestView, OIDCLogoutView

from reefauth.views import DebugOIDCAuthenticationCallbackView

urlpatterns = [
    path("health/", lambda _: HttpResponse(status=204)),
    path("admin/", admin.site.urls),
    # TODO: swap back to include("mozilla_django_oidc.urls") once the staging
    # 500 at /oidc/callback/ is diagnosed — see reefauth/views.py.
    path(
        "oidc/authenticate/",
        OIDCAuthenticationRequestView.as_view(),
        name="oidc_authentication_init",
    ),
    path(
        "oidc/callback/",
        DebugOIDCAuthenticationCallbackView.as_view(),
        name="oidc_authentication_callback",
    ),
    path("oidc/logout/", OIDCLogoutView.as_view(), name="oidc_logout"),
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
    path("manage/", include("surveys.manage_urls")),
]

if settings.DEBUG and "debug_toolbar" in settings.INSTALLED_APPS:
    urlpatterns = [path("__debug__/", include("debug_toolbar.urls"))] + urlpatterns
