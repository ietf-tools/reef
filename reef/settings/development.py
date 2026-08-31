# Copyright The IETF Trust 2026, All Rights Reserved
"""Development-mode Django settings for the Reef project."""

import os

from .base import *
from .logging.development import LOGGING as _logging

# SECURITY WARNING: insecure key for local development only.
SECRET_KEY = "django-insecure-reef-dev-key-do-not-use-in-production"

# SECURITY WARNING: never run with debug turned on in production.
DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Developer tooling.
INSTALLED_APPS += [
    "debug_toolbar",
]
MIDDLEWARE += [
    "debug_toolbar.middleware.DebugToolbarMiddleware",
]
INTERNAL_IPS = ["127.0.0.1", "localhost"]

# Red's dev server calls the Reef API from the browser.
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# Email via mailpit in the dev environment.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("REEF_EMAIL_HOST", "mailpit")
EMAIL_PORT = int(os.environ.get("REEF_EMAIL_PORT", "1025"))

# A real cache, so that anything relying on one behaves here the way it does in
# production. Base leaves this as DummyCache, under which every cache write succeeds
# and every read misses, which is how a caching bug hides until staging.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "reef-development",
    }
}

LOGGING = _logging

# Local settings override, if present.
try:
    from .development_local import *
except ImportError:
    pass
