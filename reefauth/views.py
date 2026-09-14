# Copyright The IETF Trust 2026, All Rights Reserved
"""Temporary diagnostics for the /oidc/callback/ 500 on staging.

mozilla_django_oidc's stock callback view lets an exception from the token
exchange (bad client id/secret, JWKS/network failure, ...) propagate as a bare
500 with nothing in the response, and staging's logs aren't reachable right
now. This wraps it to log the full traceback and, outside production, put the
exception itself in the response body so it's visible without log access.

TODO: revert to `mozilla_django_oidc.views.OIDCAuthenticationCallbackView`
directly (or just `include("mozilla_django_oidc.urls")` in reef/urls.py) once
the staging 500 is diagnosed.

Deliberately not gated on settings.DEPLOYMENT_MODE: a first version tried to
only show the traceback outside "production", but that assumed staging's
REEF_DEPLOYMENT_MODE is actually set to "staging" rather than defaulting to
"production" — an assumption that turned out to be wrong (or at least
unverifiable from here), which silently swallowed the debug output. This
always shows it, which does mean anyone who completes (or replays) a login
here sees a traceback until this is reverted.
"""

import logging
import traceback

from django.http import HttpResponse
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView

logger = logging.getLogger("reef")


class DebugOIDCAuthenticationCallbackView(OIDCAuthenticationCallbackView):
    def get(self, request):
        try:
            return super().get(request)
        except Exception:
            logger.exception("OIDC callback failed")
            return HttpResponse(
                "OIDC callback failed:\n\n" + traceback.format_exc(),
                status=500,
                content_type="text/plain",
            )
