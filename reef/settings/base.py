# Copyright The IETF Trust 2026, All Rights Reserved
"""Django settings for the Reef project, common to all environments."""

import os
from pathlib import Path

from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured

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
    "simple_history",
    "django_celery_beat",
    "rules.apps.AutodiscoverRulesConfig",
    "reefauth",
    "surveys",
    "ratings",
    "popularity",
    "docsets",
    "subjects",
    "subscriptions",
    "stats",
    "me",
    "precomputer",
]

MIDDLEWARE = [
    # First: it answers CORS preflights and must run before any middleware that
    # can generate a response of its own (CommonMiddleware, SecurityMiddleware).
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Serves STATIC_ROOT. Nothing in front of gunicorn does: the Cloudflare
    # Worker hands /static/ back to the origin (see client/wrangler.jsonc) and
    # the origin is this process. Documented position, directly after
    # SecurityMiddleware -- a static file is answered here and never reaches
    # CSPMiddleware, which is what we want, as a CSP header on a .js file
    # governs nothing.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "csp.middleware.CSPMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # must come after session and authentication middleware
    "mozilla_django_oidc.middleware.SessionRefresh",
    "simple_history.middleware.HistoryRequestMiddleware",
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
    "reefauth.backends.ReefOIDCAuthBackend",
    "rules.permissions.ObjectPermissionBackend",
)

# OIDC (Authentik at account.ietf.org). Every URL and credential comes from its
# own environment variable; none is derived from another.
#
# These OIDC_* settings are Reef as a *relying party*, logging staff into the
# Django admin, including the builder/analytics site nested under it at
# /admin/survey-builder/, under the "reef-admin-staging" application — the only
# interactive login Reef performs.
# Public survey-taking is never
# logged into here: it authenticates against the separate "reef-staging"
# application entirely client-side (the Nuxt runner talks to Authentik
# directly), so Reef only ever sees the resulting access token as an API
# caller — see REEF_API_OIDC_* below, a separate role with its own issuer, JWKS
# and client id.
#
# Each falls back with `or` rather than an os.environ.get default, because
# compose passes a variable through as an empty string when absent from the .env
# file: an unset variable arrives as "" and a get() default is never reached.
OIDC_OP_ISSUER_ID = (
    os.environ.get("REEF_ADMIN_OIDC_ISSUER", "")
    or "https://account.ietf.org/application/o/reef-admin-staging/"
)
OIDC_OP_AUTHORIZATION_ENDPOINT = (
    os.environ.get("REEF_ADMIN_OIDC_AUTHORIZATION_ENDPOINT", "")
    or "https://account.ietf.org/application/o/authorize/"
)
OIDC_OP_TOKEN_ENDPOINT = (
    os.environ.get("REEF_ADMIN_OIDC_TOKEN_ENDPOINT", "")
    or "https://account.ietf.org/application/o/token/"
)
OIDC_OP_USER_ENDPOINT = (
    os.environ.get("REEF_ADMIN_OIDC_USERINFO_ENDPOINT", "")
    or "https://account.ietf.org/application/o/userinfo/"
)
OIDC_OP_JWKS_ENDPOINT = (
    os.environ.get("REEF_ADMIN_OIDC_JWKS_ENDPOINT", "")
    or "https://account.ietf.org/application/o/reef-admin-staging/jwks/"
)
OIDC_OP_END_SESSION_ENDPOINT = (
    os.environ.get("REEF_ADMIN_OIDC_END_SESSION_ENDPOINT", "")
    or "https://account.ietf.org/application/o/reef-admin-staging/end-session/"
)

OIDC_RP_CLIENT_ID = os.environ.get("REEF_ADMIN_OIDC_RP_CLIENT_ID", "")
OIDC_RP_CLIENT_SECRET = os.environ.get("REEF_ADMIN_OIDC_RP_CLIENT_SECRET", "")
# The admin application's assigned signing key is EC, same as rfc-editor's (see
# REEF_API_OIDC_ALGORITHMS below) — this instance's certificates are EC by
# convention, not RSA.
OIDC_RP_SIGN_ALGO = "ES256"
# Group membership rides along in the "profile" scope's claims already — this
# Authentik instance has no scope actually named "groups" to request.
OIDC_RP_SCOPES = "openid profile email"
OIDC_STORE_ID_TOKEN = True  # kept in session for RP-initiated logout
OIDC_OP_LOGOUT_URL_METHOD = "reefauth.utils.op_logout_url"
# SessionRefresh middleware will force a reauth every EXPIRY_SECONDS (default 15 mins)
# OIDC_RENEW_ID_TOKEN_EXPIRY_SECONDS = 15 * 60

LOGIN_URL = "oidc_authentication_init"  # send @login_required through Authentik
# Every Django session Reef starts is a reef-admin login, so the fallback is the admin.
LOGIN_REDIRECT_URL = "/admin/"
LOGOUT_REDIRECT_URL = "/admin/"

# SurveyJS commercial license key for Creator and Analytics (empty in dev, which
# runs unlicensed with a watermark). Passed to the browser bundles.
REEF_SURVEYJS_LICENSE_KEY = os.environ.get("REEF_SURVEYJS_LICENSE_KEY", "")

# The largest response accepted, as compact JSON bytes: a free-text answer is the
# biggest thing a person types, and this leaves it room many times over.
REEF_SURVEY_RESPONSE_MAX_BYTES = int(
    os.environ.get("REEF_SURVEY_RESPONSE_MAX_BYTES", "65536")
)

# Reef's own public origin, scheme included and no trailing slash. Needed for one
# thing: the link Red's toast follows to the survey runner, which is Reef's own
# Nuxt client sharing this same origin, not a separate service -- see
# surveys.serializers.OpenSurveySerializer.get_url(). A precomputed file has no
# request to read this off, so it has to come from settings rather than
# request.build_absolute_uri(). No default here, like ALLOWED_HOSTS: every
# environment sets its own, because there is no value that would be right by
# accident.
REEF_SITE_URL = os.environ.get("REEF_SITE_URL")

# Bearer (resource-server) validation of Authentik access tokens.
#
# Independent of the RP login settings above. Two callers: Red's "rfc-editor"
# application, and "reef-staging", which public survey-takers sign into through
# the Nuxt runner (see above). Reef only validates the access tokens those
# applications already issued; it is never a party to either login. Each calling
# application has its own issuer and JWKS, so accepting a caller means naming
# its slug here; each also mints access tokens whose `aud` is its own client
# id, so that id has to be listed as an accepted audience.
#
# REEF_API_OIDC_ISSUERS and REEF_API_OIDC_JWKS_ENDPOINTS fall back with `or`
# rather than an os.environ.get default, because compose passes a variable
# through as an empty string when absent from the .env file: an unset variable
# arrives as "" and a get() default is never reached.
#
# REEF_API_OIDC_ISSUERS: comma-separated token issuers that are accepted, and
# REEF_API_OIDC_JWKS_ENDPOINTS: the comma-separated JWKS URL of each, in the same
# order. Defaults to the two callers Reef expects, so an unlisted caller is
# rejected rather than silently trusted. Leaving out the "reef-staging" issuer
# refuses every signed-in survey-taker, including on open surveys, where their
# token turns an otherwise anonymous request into a 401.
_api_oidc_issuers = [
    i.strip()
    for i in (
        os.environ.get("REEF_API_OIDC_ISSUERS", "")
        or "https://account.ietf.org/application/o/rfc-editor/,"
        "https://account.ietf.org/application/o/reef-staging/"
    ).split(",")
    if i.strip()
]
_api_oidc_jwks_endpoints = [
    j.strip()
    for j in (
        os.environ.get("REEF_API_OIDC_JWKS_ENDPOINTS", "")
        or "https://account.ietf.org/application/o/rfc-editor/jwks/,"
        "https://account.ietf.org/application/o/reef-staging/jwks/"
    ).split(",")
    if j.strip()
]
if len(_api_oidc_issuers) != len(_api_oidc_jwks_endpoints):
    raise ImproperlyConfigured(
        "REEF_API_OIDC_ISSUERS and REEF_API_OIDC_JWKS_ENDPOINTS must list the "
        "same number of entries, in the same order."
    )
# Issuer -> JWKS endpoint. The issuer is what the token actually carries, so
# this doubles as the trusted-issuer allowlist and as the lookup that pairs a
# token with the right verification keys.
REEF_API_OIDC_JWKS_ENDPOINTS = dict(
    zip(_api_oidc_issuers, _api_oidc_jwks_endpoints, strict=True)
)
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
# Accepted `aud` values. Unlike the slug list above, there is no sensible
# non-empty default here: a caller's client id is opaque and per-deployment, so
# naming Reef's own admin RP client (which never calls the API) would be
# meaningless rather than safe. An empty list disables audience verification —
# every real deployment must set this explicitly, which is already required in
# practice (see docs/development.md).
REEF_API_OIDC_AUDIENCES = [
    a.strip()
    for a in os.environ.get("REEF_API_OIDC_AUDIENCES", "").split(",")
    if a.strip()
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
    # Survey visibility is the only serialized field with that name. Pinned
    # anyway: VisibilityEnum is the name Red and the Nuxt client already
    # generate from, and drf-spectacular would rename it to a hash of the
    # choices as soon as a second field called visibility was exposed.
    "ENUM_NAME_OVERRIDES": {
        "VisibilityEnum": "surveys.models.SURVEY_VISIBILITY_CHOICES",
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
STATICFILES_DIRS = [
    # Hand-written assets. Named apart from STATIC_ROOT, which is the collected
    # output and is not checked in.
    BASE_DIR / "static_src",
    # Self-hosted SurveyJS bundles (populated by vendor/sync.sh via npm).
    BASE_DIR / "vendor" / "static",
]

# Hash every collected file and record the mapping in staticfiles.json, so the
# names {% static %} emits are content-addressed and whitenoise can serve them
# immutable and cached for a year. Deployed builds run collectstatic during the
# image build (dev/build/backend.Dockerfile), which is where the manifest is
# written; development overrides this back to the unhashed storage, because
# there the manifest would be stale the moment a static file is edited.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

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

# Refuses network access during tests; see the module for why.
TEST_RUNNER = "reef.test_runner.ReefTestRunner"

# Caches. Disabled by default; per-environment modules configure a real backend.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.dummy.DummyCache",
    }
}

# Email. Per-environment modules configure a real backend.
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("REEF_DEFAULT_FROM_EMAIL", "reef@ietf.org")
# The domain Message-IDs are generated in. Without it Django uses the local
# hostname, which is a pod name under Kubernetes. See reef.mail.
MESSAGE_ID_DOMAIN = os.environ.get("REEF_MESSAGE_ID_DOMAIN", "ietf.org")
# Where a notification tells its reader to go to change or stop it. Red owns
# the subscription UI, so this is Red's page rather than a Reef route. Empty
# leaves the line and the List-Unsubscribe header out rather than linking
# nowhere.
REEF_SUBSCRIPTIONS_URL = os.environ.get("REEF_SUBSCRIPTIONS_URL", "")
# Whether a digest may go out without one. False here so that development can send
# mail to mailpit without configuring Red's page; production sets it True, because a
# notification that does not tell its reader how to stop it should not be sent.
REEF_REQUIRE_UNSUBSCRIBE_URL = False
ADMINS = []

# Document metadata. Reef resolves an identifier to a title, and a subseries to its
# contents, by reading Red's public files; it stores none of it. See reef/rfcmeta.py
# and reef/schemas/README.md.
REEF_RFC_DATA_BASE_URL = os.environ.get(
    "REEF_RFC_DATA_BASE_URL", "https://www.rfc-editor.org"
)
REEF_RFC_DATA_TIMEOUT = int(os.environ.get("REEF_RFC_DATA_TIMEOUT", "30"))
# Where a notification sends a reader to read about a document. The same origin as
# the data today, and a separate setting because they are separate things: pointing
# the data URL at a staging bucket must not put staging links in somebody's mail.
REEF_RFC_SITE_URL = os.environ.get("REEF_RFC_SITE_URL", "https://www.rfc-editor.org")

# Notification delivery. A digest is written to the database before it is queued, so
# that a broker restart cannot lose it; the sweeper puts back anything still owed.
# Old enough that an in-flight attempt and its first few retries have finished, so a
# sweep does not race a delivery that is already going.
REEF_NOTIFICATION_SWEEP_AFTER_SECONDS = int(
    os.environ.get("REEF_NOTIFICATION_SWEEP_AFTER_SECONDS", "3600")
)
# After this many attempts a notification stops being offered. A message that cannot
# be sent should stop being retried rather than be re-enqueued for ever, and the row
# stays as the record that it was owed and never went.
REEF_NOTIFICATION_MAX_ATTEMPTS = int(
    os.environ.get("REEF_NOTIFICATION_MAX_ATTEMPTS", "10")
)
# How old Red's index may get before a run says so. Red rebuilds it when RFCs are
# published rather than on a clock, and publication is bursty: over the last five
# years the gaps between publication dates ran to a median of 3 days, a p95 of 12 and
# a maximum of 23. Thirty is above every gap observed in that time. This is a backstop
# anyway; the signal that matters is a document Reef holds and Red's index does not.
REEF_RFC_INDEX_MAX_AGE_DAYS = int(os.environ.get("REEF_RFC_INDEX_MAX_AGE_DAYS", "30"))
# How long the reduced index is shared for before a caller goes back to Red. An hour
# against a median three-day gap between RFC publications: fresh enough that the daily
# precomputer run never works from yesterday's series, cheap enough that an admin page
# never waits for a 6.8 MB fetch.
REEF_RFC_INDEX_CACHE_SECONDS = int(
    os.environ.get("REEF_RFC_INDEX_CACHE_SECONDS", "3600")
)

# Precomputer. Where `manage.py precompute` writes the public API responses it
# renders ahead of time, so that a reader is served a file rather than a query
# that aggregates every rating, subscription and set entry.
#
# Naming a bucket switches it to S3; leaving REEF_PRECOMPUTE_S3_BUCKET empty
# writes to REEF_PRECOMPUTE_DIR instead, which is what a developer with no
# object storage to hand gets. The endpoint is for S3-compatible services and
# is left empty for AWS itself.
REEF_PRECOMPUTE_S3_BUCKET = os.environ.get("REEF_PRECOMPUTE_S3_BUCKET", "")
REEF_PRECOMPUTE_S3_ENDPOINT = os.environ.get("REEF_PRECOMPUTE_S3_ENDPOINT", "")
REEF_PRECOMPUTE_S3_REGION = os.environ.get("REEF_PRECOMPUTE_S3_REGION", "auto")
REEF_PRECOMPUTE_S3_ACCESS_KEY_ID = os.environ.get(
    "REEF_PRECOMPUTE_S3_ACCESS_KEY_ID", ""
)
REEF_PRECOMPUTE_S3_SECRET_ACCESS_KEY = os.environ.get(
    "REEF_PRECOMPUTE_S3_SECRET_ACCESS_KEY", ""
)
REEF_PRECOMPUTE_DIR = os.environ.get("REEF_PRECOMPUTE_DIR") or BASE_DIR / "precomputed"
# Whether a run may fall back to REEF_PRECOMPUTE_DIR when no bucket is named. False
# here so that development works with no object storage; production sets it True,
# because a worker writing into its own container reports success while publishing
# nothing, which is worse than refusing.
REEF_PRECOMPUTE_REQUIRE_S3 = False
# Parallel uploads. Rendering is serial database work; this is how many of the
# resulting files are in flight to the store at once.
REEF_PRECOMPUTE_CONCURRENCY = int(os.environ.get("REEF_PRECOMPUTE_CONCURRENCY", "8"))

# Red's precompute-multiple EventListener, taking {"rfcs": "9110,9111", "skipIndices":
# "true"}. Empty: Red is not told and picks the change up on its own daily run.
REEF_TRIGGER_RED_PRECOMPUTE_URL = os.environ.get("REEF_TRIGGER_RED_PRECOMPUTE_URL", "")
# How long a document must go unwritten before its change is pushed, so that a
# burst of writes costs one run.
REEF_DOCUMENT_CHANGE_QUIET_SECONDS = int(
    os.environ.get("REEF_DOCUMENT_CHANGE_QUIET_SECONDS", "60")
)
# RFC numbers per request to Red; one pipeline run per batch.
REEF_RED_PRECOMPUTE_BATCH_SIZE = int(
    os.environ.get("REEF_RED_PRECOMPUTE_BATCH_SIZE", "100")
)

# Celery
CELERY_TIMEZONE = "UTC"
CELERY_BROKER_URL = os.environ.get("REEF_BROKER_URL", "amqp://mq/")
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_IGNORE_RESULT = True

# Schedules live in the database (django-celery-beat). DatabaseScheduler writes each
# entry in CELERY_BEAT_SCHEDULE below over the database row of the same name every
# time beat starts, so these are retimed here: an admin edit to one of them lasts
# until the next restart. Entries added only in the admin are not touched.
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# The precomputer gets its own queue. A full run holds a worker for as long as it
# takes and, once it resolves document metadata, a few megabytes of parsed index with
# it; sharing the default queue would put that in front of subscription mail, where
# the delay is a person waiting for a message.
CELERY_TASK_ROUTES = {
    "precomputer.tasks.*": {"queue": "precompute"},
    "popularity.tasks.*": {"queue": "precompute"},
}

CELERY_BEAT_SCHEDULE = {
    # Daily. This is the only job that notices an RFC Red has published and Reef has
    # never seen, since nothing in Reef's own tables moves when that happens.
    "precompute-all": {
        "task": "precomputer.tasks.precompute_all",
        "schedule": crontab(hour="3", minute="0"),
    },
    # Hourly. Ratings, subscriptions and set entries arrive from readers all day, and
    # these are the two files that change when they do.
    "precompute-engagement": {
        "task": "precomputer.tasks.precompute_engagement",
        "schedule": crontab(minute="20"),
    },
    # Every five minutes: the documents readers changed, once quiet, then Red is told.
    "push-document-changes": {
        "task": "precomputer.tasks.push_document_changes",
        "schedule": crontab(minute="*/5"),
    },
    # Hourly, so a publication reaches the web feed within about two hours of Red's
    # index reflecting it: up to an hour until the next run, plus the index's cache
    # of up to REEF_RFC_INDEX_CACHE_SECONDS. The mail is the daily digest below.
    "detect-rfc-changes": {
        "task": "subscriptions.tasks.detect_rfc_changes",
        "schedule": crontab(minute="25"),
    },
    # Daily, just after that hour's detection, so the digest holds everything
    # staged since yesterday's. Publication is bursty, and one mail a day gathers a
    # burst into one.
    "send-digest": {
        "task": "subscriptions.tasks.send_digest",
        "schedule": crontab(hour="4", minute="30"),
    },
    # Hourly. Recovers notifications that were written down but never delivered,
    # which is what the database row exists for.
    "sweep-unsent-notifications": {
        "task": "subscriptions.tasks.sweep_unsent_notifications",
        "schedule": crontab(minute="40"),
    },
}
