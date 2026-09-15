# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Reef user, authenticated through Authentik OIDC.

    A local superuser (created with createsuperuser) is retained as a
    break-glass account for when the identity provider is unavailable.
    """

    name = models.CharField(
        max_length=255,
        blank=True,
        help_text="User's display name",
    )

    oidc_sub = models.CharField(
        max_length=255,  # OpenID Core 1.0 limits the subject to 255 ASCII chars
        null=True,
        unique=True,
        help_text="Authentik subject identifier (the OIDC 'sub' claim)",
    )

    # Unbounded: Authentik's `picture` claim can be a data: URI embedding a
    # generated avatar image, easily running to several KB — not just a link.
    avatar = models.TextField(blank=True)

    def get_username(self):
        """Prefer a human-readable identifier for display.

        `username` itself stays the opaque `authentik-<sub>` value (see
        sync_user_from_claims) so it's stable even if a claim changes; this is
        only what shows up in messages like the admin login page's "you are
        authenticated as ...".
        """
        return self.name or self.email or super().get_username()

    def __str__(self):
        return self.get_username()
