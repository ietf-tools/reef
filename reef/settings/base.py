# Copyright The IETF Trust 2026, All Rights Reserved
"""Django settings for the Reef project, common to all environments."""

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
    "corsheaders",
    "rules.apps.AutodiscoverRulesConfig",
    "reefauth",
    "surveys",
    "ratings",
    "popularity",
    "docsets",
    "subscriptions",
    "stats",
]

MIDDLEWARE = [
    # First: it answers CORS preflights and must run before any middleware that
    # can generate a response of its own (CommonMiddleware, SecurityMiddleware).
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "csp.middleware.CSPMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "reef.urls"

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

WSGI_APPLICATION = "reef.wsgi.application"

# Authentication
AUTH_USER_MODEL = "reefauth.User"
AUTHENTICATION_BACKENDS = (
    "reefauth.backends.ReefOIDCAuthBackend",  # Authentik OIDC login
    "rules.permissions.ObjectPermissionBackend",  # rules-based permissions
    "django.contrib.auth.backends.ModelBackend",  # break-glass local superuser
)

# OIDC (Authentik at account.ietf.org). Endpoints are derived from the host and
# the per-application slug; credentials come from the environment.
#
# Two distinct roles, deliberately configured apart (see REEF_API_OIDC_* below):
# these OIDC_* settings are Reef as a *relying party*, logging its own staff into
# the builder/analytics site. Validating access tokens from API callers is a
# separate role with a separate application, because a caller like Red is its own
# Authentik application and so presents a different issuer, JWKS and client id.
REEF_OIDC_HOST = os.environ.get("REEF_OIDC_HOST", "https://account.ietf.org")
REEF_OIDC_APP_SLUG = os.environ.get("REEF_OIDC_APP_SLUG", "reef")
_oidc_app = f"{REEF_OIDC_HOST}/application/o"
OIDC_OP_ISSUER_ID = f"{_oidc_app}/{REEF_OIDC_APP_SLUG}/"
OIDC_OP_AUTHORIZATION_ENDPOINT = f"{_oidc_app}/authorize/"
OIDC_OP_TOKEN_ENDPOINT = f"{_oidc_app}/token/"
OIDC_OP_USER_ENDPOINT = f"{_oidc_app}/userinfo/"
OIDC_OP_JWKS_ENDPOINT = f"{_oidc_app}/{REEF_OIDC_APP_SLUG}/jwks/"
OIDC_OP_END_SESSION_ENDPOINT = f"{_oidc_app}/{REEF_OIDC_APP_SLUG}/end-session/"

OIDC_RP_CLIENT_ID = os.environ.get("REEF_OIDC_RP_CLIENT_ID", "")
OIDC_RP_CLIENT_SECRET = os.environ.get("REEF_OIDC_RP_CLIENT_SECRET", "")
OIDC_RP_SIGN_ALGO = "RS256"
OIDC_RP_SCOPES = "openid profile email"
OIDC_STORE_ID_TOKEN = True  # kept in session for RP-initiated logout
OIDC_OP_LOGOUT_URL_METHOD = "reefauth.utils.op_logout_url"

LOGIN_URL = "oidc_authentication_init"  # send @login_required through Authentik
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# SurveyJS commercial license key for Creator and Analytics (empty in dev, which
# runs unlicensed with a watermark). Passed to the browser bundles.
REEF_SURVEYJS_LICENSE_KEY = os.environ.get("REEF_SURVEYJS_LICENSE_KEY", "")

# Base URL of the Nuxt survey runner, used to build the link Red follows from
# its popover. Empty yields a site-relative "/s/<slug>".
REEF_SURVEY_RUNNER_BASE_URL = os.environ.get("REEF_SURVEY_RUNNER_BASE_URL", "")

# Bearer (resource-server) validation of Authentik access tokens.
#
# Independent of the RP login settings above, and list-valued, because more than
# one Authentik application calls this API: the survey runner lives under
# REEF_OIDC_APP_SLUG, while Red is the separate "rfc-editor" application. Each
# application has its own issuer and JWKS, so accepting a caller means naming its
# slug here; each also mints access tokens whose `aud` is its own client id, so
# that id has to be listed as an accepted audience.
#
# Both settings fall back with `or` rather than an os.environ.get default,
# because compose passes them through as empty strings when they are absent from
# the .env file: an unset variable arrives as "" and a get() default is never
# reached.
#
# REEF_API_OIDC_APP_SLUGS: comma-separated Authentik application slugs whose
# tokens are accepted. Defaults to Reef's own application alone, so an unlisted
# caller is rejected rather than silently trusted.
REEF_API_OIDC_APP_SLUGS = [
    s.strip()
    for s in (
        os.environ.get("REEF_API_OIDC_APP_SLUGS", "") or REEF_OIDC_APP_SLUG
    ).split(",")
    if s.strip()
]
# Issuer -> JWKS endpoint for those applications. The issuer is what the token
# actually carries, so this doubles as the trusted-issuer allowlist and as the
# lookup that pairs a token with the right verification keys.
REEF_API_OIDC_JWKS_ENDPOINTS = {
    f"{_oidc_app}/{slug}/": f"{_oidc_app}/{slug}/jwks/"
    for slug in REEF_API_OIDC_APP_SLUGS
}
# Accepted signature algorithms, as a set rather than the single
# OIDC_RP_SIGN_ALGO used for RP login: Authentik chooses the signing key per
# application, so callers legitimately differ. The rfc-editor application signs
# with ES256 while RS256 is the more common default, so both are accepted.
# Symmetric and unsigned algorithms are rejected by the authenticator whatever
# is configured here — see reefauth.authentication.
REEF_API_OIDC_ALGORITHMS = [
    a.strip()
    for a in (os.environ.get("REEF_API_OIDC_ALGORITHMS", "") or "RS256,ES256").split(
        ","
    )
    if a.strip()
]
# Accepted `aud` values. Defaults to the RP client id to preserve the previous
# single-caller behaviour. An empty list disables audience verification.
REEF_API_OIDC_AUDIENCES = [
    a.strip()
    for a in os.environ.get(
        "REEF_API_OIDC_AUDIENCES", os.environ.get("REEF_OIDC_AUDIENCE", "")
    ).split(",")
    if a.strip()
] or ([OIDC_RP_CLIENT_ID] if OIDC_RP_CLIENT_ID else [])

# The groups claim maps to staff access.
REEF_OIDC_GROUPS_CLAIM = os.environ.get("REEF_OIDC_GROUPS_CLAIM", "groups")
REEF_OIDC_STAFF_GROUPS = [
    g.strip()
    for g in os.environ.get("REEF_OIDC_STAFF_GROUPS", "").split(",")
    if g.strip()
]

# Database
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "reef"),
        "USER": os.environ.get("POSTGRES_USER", "reef"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "reef"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

# Django REST Framework
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "reefauth.authentication.BearerTokenAuthentication",
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

# CORS. Red is served from its own origin and calls the Reef API from the
# browser, so those responses need Access-Control-Allow-Origin. Only the API is
# cross-origin; the builder, admin and Nuxt runner all share the NGINX origin,
# so confine the headers to /api/reef/ rather than the whole site.
CORS_URLS_REGEX = r"^/api/reef/.*$"

# Set per environment: dev allows Red's dev server, production reads the
# deployment's origins from the environment. Empty means no cross-origin access.
CORS_ALLOWED_ORIGINS = []

# The APIs authenticate as a resource server via an Authorization bearer JWT
# (see REST_FRAMEWORK above), never via the Django session cookie. Leaving
# credentials off keeps browsers from attaching Reef cookies to Red's requests.
CORS_ALLOW_CREDENTIALS = False

SPECTACULAR_SETTINGS = {
    "TITLE": "Reef",
    "DESCRIPTION": "Backend API for the Reef survey and engagement service",
    "VERSION": "0.1.0",
    "SCHEMA_PATH_PREFIX": "/api/reef/",
    # Surveys and document sets both have a "visibility", with different
    # choices, and drf-spectacular resolves that collision by naming both after
    # a hash of their choices. Pinned here instead: VisibilityEnum is the name
    # Red and the Nuxt client already generate from, and a hashed name would
    # change again the next time either set of choices did.
    "ENUM_NAME_OVERRIDES": {
        "VisibilityEnum": "surveys.models.SURVEY_VISIBILITY_CHOICES",
        "DocumentSetVisibilityEnum": "docsets.models.DOCUMENT_SET_VISIBILITY_CHOICES",
    },
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
DEFAULT_FROM_EMAIL = os.environ.get("REEF_DEFAULT_FROM_EMAIL", "reef@ietf.org")
ADMINS = []

# Celery
CELERY_TIMEZONE = "UTC"
CELERY_BROKER_URL = os.environ.get("REEF_BROKER_URL", "amqp://mq/")
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_IGNORE_RESULT = True  # ignore results unless a task opts in
