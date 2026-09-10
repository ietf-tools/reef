# Copyright The IETF Trust 2026, All Rights Reserved
"""System checks on who may call the API."""

from django.conf import settings
from django.core.checks import Tags, Warning, register

# Modes deployed behind Red, which calls the API from its own origin.
_CROSS_ORIGIN_MODES = ("staging", "production")


@register(Tags.security)
def cors_origins_configured(app_configs, **kwargs):
    """Warn when a deployment Red must reach cross-origin allows no origin at all.

    An empty list fails every request Red makes at the preflight, and nothing on
    the server side records that: the browser discards the response before the
    page can read it. The list comes from REEF_CORS_ALLOWED_ORIGINS, which a new
    environment can leave out without anything else failing.
    """
    if settings.DEPLOYMENT_MODE not in _CROSS_ORIGIN_MODES:
        return []
    if settings.CORS_ALLOWED_ORIGINS or getattr(
        settings, "CORS_ALLOWED_ORIGIN_REGEXES", []
    ):
        return []
    return [
        Warning(
            "CORS_ALLOWED_ORIGINS is empty, so no browser origin (Red) can call "
            "the API.",
            hint="Set REEF_CORS_ALLOWED_ORIGINS to Red's origin for this environment.",
            id="reefauth.W001",
        )
    ]
