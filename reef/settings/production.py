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


SECRET_KEY = os.environ["REEF_DJANGO_SECRET_KEY"]
assert not SECRET_KEY.startswith("django-insecure")  # never the dev key

DEBUG = False

# REEF_ALLOWED_HOSTS is a newline-separated list of allowed hosts.
ALLOWED_HOSTS = _multiline_to_list(os.environ["REEF_ALLOWED_HOSTS"])

# The kubelet addresses its probes to the pod's own IP, which Django then sees as the
# Host header. That address is assigned at scheduling time, so the pod passes it in
# through the downward API rather than it being something a deployment can configure.
_pod_ip = os.environ.get("POD_IP")
if _pod_ip:
    ALLOWED_HOSTS.append(_pod_ip)

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

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Short on purpose: an HSTS mistake is correctable only by waiting out what browsers
# have already cached.
SECURE_HSTS_SECONDS = 3600
# Adding the rfc-editor.org apex to REEF_ALLOWED_HOSTS would extend this to every
# subdomain of the apex, including hosts this service knows nothing about.
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# SECURE_HSTS_PRELOAD stays off: getting back off the preload list takes months, and it
# commits an apex that belongs to more than this service.

# The kubelet probe reaches this process over plain http, addressed to the pod IP.
# Unexempted, Django answers it with a 301, which Kubernetes counts as a pass (anything
# under 400), leaving the probe reporting health without testing it.
SECURE_SSL_REDIRECT = True
# `/?` because the redirect is decided before URL resolution, so APPEND_SLASH never
# gets to normalise a probe pointed at /health.
SECURE_REDIRECT_EXEMPT = [r"^health/?$"]
