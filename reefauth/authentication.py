# Copyright The IETF Trust 2026, All Rights Reserved
"""DRF resource-server authentication for Authentik bearer tokens.

Validates a JWT access token from the Authorization header against the issuing
application's JWKS, then maps it to a Reef User. Returns None when no bearer
token is present so the same class works on both protected endpoints (paired
with IsAuthenticated) and public endpoints that merely want to identify a user
when a token happens to be supplied (for example the open-survey list).

Several Authentik applications call this API (the survey runner, and Red as the
separate "rfc-editor" application), each with its own issuer, JWKS and client
id. Which are accepted is configured by REEF_API_OIDC_APP_SLUGS and
REEF_API_OIDC_AUDIENCES — deliberately separate from the OIDC_* settings that
log Reef's own staff into the builder site, which is an unrelated role that
happens to involve the same identity provider.
"""

from functools import lru_cache

import jwt
from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import authentication, exceptions

from .claims import sync_user_from_claims


# Asymmetric algorithms only, enforced regardless of configuration. Accepting an
# HS* algorithm alongside these is the classic key-confusion attack: the verifying
# key is the OP's *public* key, which anyone can fetch from the JWKS, so a caller
# could mint its own token by using that public key as the HMAC secret. "none" is
# excluded for the obvious reason.
_ASYMMETRIC_ALGORITHMS = frozenset(
    {
        "RS256",
        "RS384",
        "RS512",
        "ES256",
        "ES384",
        "ES512",
        "PS256",
        "PS384",
        "PS512",
        "EdDSA",
    }
)


@lru_cache(maxsize=None)
def _jwks_client(jwks_endpoint):
    """One JWKS client per endpoint, reused across requests.

    PyJWKClient caches the fetched JWK set on the instance for its default
    `lifespan`, so building one per request (as this module used to) refetched
    the JWKS from Authentik on every authenticated call. The set of endpoints is
    bounded by configuration, so this cache cannot grow unboundedly.
    """
    return jwt.PyJWKClient(jwks_endpoint)


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

    def get_issuer(self, token):
        """The token's `iss`, checked against the accepted-issuer allowlist.

        Read without verifying the signature, which is safe only because the
        value is used purely to look up an entry in trusted configuration: an
        unrecognised issuer is rejected here, and a recognised one merely selects
        the JWKS that then has to actually verify the signature. No other claim
        from this payload may be trusted.
        """
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise exceptions.AuthenticationFailed(
                f"Invalid bearer token: {exc}"
            ) from exc
        issuer = unverified.get("iss")
        if issuer not in settings.REEF_API_OIDC_JWKS_ENDPOINTS:
            raise exceptions.AuthenticationFailed(
                f"Bearer token from an issuer this API does not accept: {issuer!r}. "
                "Add its Authentik application slug to REEF_API_OIDC_APP_SLUGS."
            )
        return issuer

    def get_signing_key(self, token, issuer):
        jwks_client = _jwks_client(settings.REEF_API_OIDC_JWKS_ENDPOINTS[issuer])
        return jwks_client.get_signing_key_from_jwt(token).key

    def get_algorithms(self):
        """The configured signature algorithms, filtered to asymmetric ones.

        Deliberately not OIDC_RP_SIGN_ALGO, which is the algorithm Reef's own
        login expects from its own application. Callers are different
        applications and Authentik signs each with whatever key that application
        was given, so this is a set — rfc-editor uses ES256, for instance.
        """
        permitted = [
            a
            for a in getattr(settings, "REEF_API_OIDC_ALGORITHMS", [])
            if a in _ASYMMETRIC_ALGORITHMS
        ]
        if not permitted:
            raise exceptions.AuthenticationFailed(
                "No usable signature algorithms configured. Set "
                "REEF_API_OIDC_ALGORITHMS to the asymmetric algorithms the "
                "calling applications sign with, such as RS256 or ES256."
            )
        return permitted

    def decode(self, token):
        issuer = self.get_issuer(token)
        audiences = list(getattr(settings, "REEF_API_OIDC_AUDIENCES", []))
        return jwt.decode(
            token,
            self.get_signing_key(token, issuer),
            algorithms=self.get_algorithms(),
            audience=audiences or None,
            # Pinned to the single issuer whose keys signed this token rather than
            # to the whole allowlist, so a token from one accepted application
            # can't be presented as though it came from another.
            issuer=issuer,
            options={"verify_aud": bool(audiences)},
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


class BearerTokenScheme(OpenApiAuthenticationExtension):
    """Document BearerTokenAuthentication as HTTP bearer (JWT) in the schema."""

    target_class = "reefauth.authentication.BearerTokenAuthentication"
    name = "BearerAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
