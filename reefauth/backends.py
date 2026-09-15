# Copyright The IETF Trust 2026, All Rights Reserved
"""OIDC login backend for the Django admin and builder/analytics site."""

from mozilla_django_oidc.auth import OIDCAuthenticationBackend

from .claims import sync_user_from_claims


class ReefOIDCAuthBackend(OIDCAuthenticationBackend):
    """Authenticate against Authentik and map claims to a Reef User by subject."""

    def filter_users_by_claims(self, claims):
        sub = claims.get("sub")
        if not sub:
            return self.UserModel.objects.none()
        return self.UserModel.objects.filter(oidc_sub=sub)

    def _stash_debug_claims(self, claims):
        # TEMP: read by reefauth.views.DebugOIDCAuthenticationCallbackView to
        # show what Authentik actually sent — see that module's docstring.
        self.request.oidc_debug_claims = claims

    def create_user(self, claims):
        self._stash_debug_claims(claims)
        return sync_user_from_claims(claims)

    def update_user(self, user, claims):
        self._stash_debug_claims(claims)
        return sync_user_from_claims(claims)
