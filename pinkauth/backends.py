# Copyright The IETF Trust 2026, All Rights Reserved
"""OIDC login backend for the Django builder and analytics site."""

from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from .claims import sync_user_from_claims


class PinkOIDCAuthBackend(OIDCAuthenticationBackend):
    """Authenticate against Authentik and map claims to a Pink User by subject."""

    def filter_users_by_claims(self, claims):
        sub = claims.get("sub")
        if not sub:
            return self.UserModel.objects.none()
        return self.UserModel.objects.filter(oidc_sub=sub)

    def create_user(self, claims):
        return sync_user_from_claims(claims)

    def update_user(self, user, claims):
        return sync_user_from_claims(claims)
