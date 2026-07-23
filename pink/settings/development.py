# Copyright The IETF Trust 2026, All Rights Reserved
"""Development-mode Django settings for the Pink project."""

from .base import *  # noqa: F401,F403

# SECURITY WARNING: insecure key for local development only.
SECRET_KEY = "django-insecure-pink-dev-key-do-not-use-in-production"

# SECURITY WARNING: never run with debug turned on in production.
DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Local settings override, if present.
try:
    from .development_local import *  # noqa: F401,F403
except ImportError:
    pass
