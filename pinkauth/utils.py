# Copyright The IETF Trust 2026, All Rights Reserved
import warnings
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings


def op_logout_url(request):
    """Construct the URI for initiating logout at the OIDC provider."""
    end_session_endpoint = getattr(settings, "OIDC_OP_END_SESSION_ENDPOINT", None)
    logout_redirect_url = getattr(settings, "LOGOUT_REDIRECT_URL", "/")
    if end_session_endpoint is None:
        return logout_redirect_url

    endpoint_parts = urlsplit(end_session_endpoint)

    if settings.DEPLOYMENT_MODE == "production" and endpoint_parts.scheme != "https":
        warnings.warn(
            "OIDC_OP_END_SESSION_ENDPOINT must be an https URI. "
            "Not initiating logout from the OP.",
            stacklevel=2,
        )
        return logout_redirect_url

    query_params = parse_qsl(endpoint_parts.query)
    if any(
        name in ["client_id", "post_logout_redirect_url", "id_token_hint"]
        for name, _ in query_params
    ):
        warnings.warn(
            "OIDC_OP_END_SESSION_ENDPOINT has an inappropriate query param. "
            "Not initiating logout from the OP.",
            stacklevel=2,
        )
        return logout_redirect_url

    query_params.append(
        ("post_logout_redirect_uri", request.build_absolute_uri(logout_redirect_url)),
    )
    id_token = request.session.get("oidc_id_token", None)
    if id_token is not None:
        query_params.append(("id_token_hint", id_token))
    return urlunsplit(endpoint_parts._replace(query=urlencode(query_params)))
