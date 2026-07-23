# Copyright The IETF Trust 2026, All Rights Reserved
"""DRF resource-server authentication for Authentik bearer tokens.

Validates a JWT access token from the Authorization header against the OP's
JWKS, then maps it to a Pink User. Returns None when no bearer token is present
so the same class works on both protected endpoints (paired with
IsAuthenticated) and public endpoints that merely want to identify a user when
a token happens to be supplied (for example the open-survey list).
"""

import jwt
from django.conf import settings
from rest_framework import authentication, exceptions

from .claims import sync_user_from_claims


class BearerTokenAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def get_token(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None
        if len(header) == 1:
            raise exceptions.AuthenticationFailed(
                "Invalid bearer header: no credentials provided."
            )
        if len(header) > 2:
            raise exceptions.AuthenticationFailed(
                "Invalid bearer header: token string may not contain spaces."
            )
        return header[1].decode()

    def get_signing_key(self, token):
        jwks_client = jwt.PyJWKClient(settings.OIDC_OP_JWKS_ENDPOINT)
        return jwks_client.get_signing_key_from_jwt(token).key

    def decode(self, token):
        audience = getattr(settings, "PINK_OIDC_AUDIENCE", "") or None
        return jwt.decode(
            token,
            self.get_signing_key(token),
            algorithms=[getattr(settings, "OIDC_RP_SIGN_ALGO", "RS256")],
            audience=audience,
            issuer=getattr(settings, "OIDC_OP_ISSUER_ID", None),
            options={"verify_aud": bool(audience)},
        )

    def authenticate(self, request):
        token = self.get_token(request)
        if token is None:
            return None  # no bearer token: defer to other authenticators / anon
        try:
            payload = self.decode(token)
        except jwt.PyJWTError as exc:
            raise exceptions.AuthenticationFailed(
                f"Invalid bearer token: {exc}"
            ) from exc
        user = sync_user_from_claims(payload)
        if user is None:
            raise exceptions.AuthenticationFailed("Bearer token has no subject claim.")
        return (user, payload)

    def authenticate_header(self, request):
        return self.keyword
