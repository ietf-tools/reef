# Copyright The IETF Trust 2026, All Rights Reserved
"""Build-mode Django settings for the Reef project.

Minimal settings so management commands (OpenAPI schema generation,
collectstatic) can run during Docker image builds. Not for running the app.
"""

import os

from .base import *

if os.environ.get("REEF_DEPLOYMENT_MODE") != "build":
    raise RuntimeError("build settings are only for use when building")

SECRET_KEY = "django-insecure-build-only-key"
DEBUG = False
ALLOWED_HOSTS = []

# No database connection is made during builds, but the engine still has to be the
# real one: drf-spectacular reads integer bounds from the backend, and sqlite would
# publish PositiveIntegerField as int64 where Postgres produces int32. Named after
# nothing, because nothing connects.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "unused-during-build",
    }
}
