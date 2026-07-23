# Copyright The IETF Trust 2026, All Rights Reserved
"""Build-mode Django settings for the Pink project.

Minimal settings so management commands (OpenAPI schema generation,
collectstatic) can run during Docker image builds. Not for running the app.
"""

import os

from .base import *

if os.environ.get("PINK_DEPLOYMENT_MODE") != "build":
    raise RuntimeError("build settings are only for use when building")

SECRET_KEY = "django-insecure-build-only-key"
DEBUG = False
ALLOWED_HOSTS = []

# No database connection is made during builds; use a local sqlite file.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "build.sqlite3",
    }
}
