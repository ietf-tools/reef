# Copyright The IETF Trust 2026, All Rights Reserved
"""Django settings for the Pink project, common to all environments."""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / "subdir".
BASE_DIR = Path(__file__).resolve().parent.parent.parent

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "mozilla_django_oidc",  # load after django.contrib.auth
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "rules.apps.AutodiscoverRulesConfig",
    "pinkauth",
    "surveys",
    "ratings",
    "popularity",
    "subscriptions",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "csp.middleware.CSPMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "pink.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "pink.wsgi.application"

# Authentication
AUTH_USER_MODEL = "pinkauth.User"
AUTHENTICATION_BACKENDS = (
    "pinkauth.backends.PinkOIDCAuthBackend",  # Authentik OIDC login
    "rules.permissions.ObjectPermissionBackend",  # rules-based permissions
    "django.contrib.auth.backends.ModelBackend",  # break-glass local superuser
)

# OIDC (Authentik at account.ietf.org). Endpoints are derived from the host and
# the per-application slug; credentials come from the environment.
PINK_OIDC_HOST = os.environ.get("PINK_OIDC_HOST", "https://account.ietf.org")
PINK_OIDC_APP_SLUG = os.environ.get("PINK_OIDC_APP_SLUG", "pink")
_oidc_app = f"{PINK_OIDC_HOST}/application/o"
OIDC_OP_ISSUER_ID = f"{_oidc_app}/{PINK_OIDC_APP_SLUG}/"
OIDC_OP_AUTHORIZATION_ENDPOINT = f"{_oidc_app}/authorize/"
OIDC_OP_TOKEN_ENDPOINT = f"{_oidc_app}/token/"
OIDC_OP_USER_ENDPOINT = f"{_oidc_app}/userinfo/"
OIDC_OP_JWKS_ENDPOINT = f"{_oidc_app}/{PINK_OIDC_APP_SLUG}/jwks/"
OIDC_OP_END_SESSION_ENDPOINT = f"{_oidc_app}/{PINK_OIDC_APP_SLUG}/end-session/"

OIDC_RP_CLIENT_ID = os.environ.get("PINK_OIDC_RP_CLIENT_ID", "")
OIDC_RP_CLIENT_SECRET = os.environ.get("PINK_OIDC_RP_CLIENT_SECRET", "")
OIDC_RP_SIGN_ALGO = "RS256"
OIDC_RP_SCOPES = "openid profile email"
OIDC_STORE_ID_TOKEN = True  # kept in session for RP-initiated logout
OIDC_OP_LOGOUT_URL_METHOD = "pinkauth.utils.op_logout_url"

LOGIN_URL = "oidc_authentication_init"  # send @login_required through Authentik
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# SurveyJS commercial license key for Creator and Analytics (empty in dev, which
# runs unlicensed with a watermark). Passed to the browser bundles.
PINK_SURVEYJS_LICENSE_KEY = os.environ.get("PINK_SURVEYJS_LICENSE_KEY", "")

# Base URL of the Nuxt survey runner, used to build the link Red follows from
# its popover. Empty yields a site-relative "/s/<slug>".
PINK_SURVEY_RUNNER_BASE_URL = os.environ.get("PINK_SURVEY_RUNNER_BASE_URL", "")

# Bearer (resource-server) validation of Authentik access tokens.
# Audience defaults to the RP client id; the groups claim maps to staff access.
PINK_OIDC_AUDIENCE = os.environ.get("PINK_OIDC_AUDIENCE", "") or OIDC_RP_CLIENT_ID
PINK_OIDC_GROUPS_CLAIM = os.environ.get("PINK_OIDC_GROUPS_CLAIM", "groups")
PINK_OIDC_STAFF_GROUPS = [
    g.strip()
    for g in os.environ.get("PINK_OIDC_STAFF_GROUPS", "").split(",")
    if g.strip()
]

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "pink"),
        "USER": os.environ.get("POSTGRES_USER", "pink"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "pink"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "pinkauth.authentication.BearerTokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Pink",
    "DESCRIPTION": "Backend API for the Pink survey and engagement service",
    "VERSION": "0.1.0",
    "SCHEMA_PATH_PREFIX": "/api/pink/",
}

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "static"
# Self-hosted SurveyJS bundles (populated by vendor/sync.sh via npm).
STATICFILES_DIRS = [BASE_DIR / "vendor" / "static"]

# Content Security Policy (django-csp). Strict, self-only: the SurveyJS Creator
# and Analytics bundles are self-hosted, not loaded from a CDN. SurveyJS injects
# <style> elements at runtime, so inline styles are permitted; scripts are not.
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'", "'unsafe-inline'"],
        "img-src": ["'self'", "data:"],
        "font-src": ["'self'", "data:"],
        "connect-src": ["'self'"],
        "frame-ancestors": ["'none'"],
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Caches. Disabled by default; per-environment modules configure a real backend.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache",
    }
}

# Email. Per-environment modules configure a real backend.
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("PINK_DEFAULT_FROM_EMAIL", "pink@ietf.org")
ADMINS = []

# Celery
CELERY_TIMEZONE = "UTC"
CELERY_BROKER_URL = os.environ.get("PINK_BROKER_URL", "amqp://mq/")
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_IGNORE_RESULT = True  # ignore results unless a task opts in
