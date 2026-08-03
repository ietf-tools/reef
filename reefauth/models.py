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

    avatar = models.URLField(blank=True)
