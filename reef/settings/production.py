# Copyright The IETF Trust 2026, All Rights Reserved
"""Production-mode Django settings for the Reef project."""

import os
from email.utils import parseaddr
from hashlib import sha384

from .base import *
from .logging.production import LOGGING as _logging

LOGGING = _logging


def _multiline_to_list(value):
    """Split a newline-separated environment value into a list."""
    return [item.strip() for item in value.split("\n") if item.strip()]


# SECURITY WARNING: keep the secret key secret.
SECRET_KEY = os.environ["REEF_DJANGO_SECRET_KEY"]
assert not SECRET_KEY.startswith("django-insecure")  # never the dev key

DEBUG = False

# REEF_ALLOWED_HOSTS is a newline-separated list of allowed hosts.
ALLOWED_HOSTS = _multiline_to_list(os.environ["REEF_ALLOWED_HOSTS"])

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["REEF_DB_NAME"],
        "USER": os.environ["REEF_DB_USER"],
        "PASSWORD": os.environ["REEF_DB_PASS"],
        "HOST": os.environ["REEF_DB_HOST"],
        "PORT": int(os.environ.get("REEF_DB_PORT", "5432")),
    }
}

# No notification goes out without a way to stop it. Reef has no unsubscribe route of
# its own -- Red owns the subscription UI -- so an unset REEF_SUBSCRIPTIONS_URL means
# mail with no opt-out, which is not a thing to discover after it has been sent.
# Digests wait, in the database, until this is configured.
REEF_REQUIRE_UNSUBSCRIBE_URL = True

# The precomputer publishes for Red to read, so a deployment that has not been given
# a bucket is misconfigured rather than opting out. Falling back to a directory inside
# an ephemeral worker would log a successful run every hour and publish nothing.
REEF_PRECOMPUTE_REQUIRE_S3 = True

# Caches. Use the memcached service if the k8s environment provides one.
_memcached_host = os.environ.get("MEMCACHED_SERVICE_HOST")
if _memcached_host is not None:
    _memcached_port = os.environ.get("MEMCACHED_SERVICE_PORT", "11211")
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.memcached.PyMemcacheCache",
            "LOCATION": f"{_memcached_host}:{_memcached_port}",
            "KEY_PREFIX": "ietf:reef",
            "KEY_FUNCTION": lambda key, key_prefix, version: (
                f"{key_prefix}:{version}:{sha384(str(key).encode('utf8')).hexdigest()}"
            ),
            "TIMEOUT": 600,
        }
    }

# Email. Configure via REEF_EMAIL_* or fall back to a k8s mailpit service.
_email_host = os.environ.get("REEF_EMAIL_HOST") or os.environ.get(
    "MAILPIT_SERVICE_HOST"
)
if _email_host is not None:
    _email_port = os.environ.get("REEF_EMAIL_PORT") or os.environ.get(
        "MAILPIT_SERVICE_PORT"
    )
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = _email_host
    if _email_port is not None:
        EMAIL_PORT = int(_email_port)

# Admins, from a newline-separated REEF_ADMINS value.
_admins = os.environ.get("REEF_ADMINS")
if _admins is not None:
    ADMINS = [parseaddr(admin) for admin in _multiline_to_list(_admins)]

# REEF_CORS_ALLOWED_ORIGINS is a newline-separated list of origins allowed to
# call the API from a browser (Red). Unset means no cross-origin access.
_cors_origins = os.environ.get("REEF_CORS_ALLOWED_ORIGINS")
if _cors_origins is not None:
    CORS_ALLOWED_ORIGINS = _multiline_to_list(_cors_origins)

# Behind a TLS-terminating proxy in staging and production.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
