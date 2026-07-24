# Development

## Running the stack

Pink runs in Docker. Use the VS Code Dev Container, or run `docker/run` for a
tmux-based session. Both bring up:

- `db` - PostgreSQL
- `app` - Django (`runserver` on 8001), the Nuxt dev server (3000), and NGINX
  (8088) which fronts both
- `celery` - background worker
- `mq` - RabbitMQ broker
- `mailpit` - captures outgoing email (web UI on 8025)
- `pgadmin` - database UI (under `/pgadmin/`)

The app image builds `FROM ghcr.io/ietf-tools/pink-app-base`, produced from
`docker/base.Dockerfile`. Until CI publishes it, build it locally and tag it
with that name.

## Deployment modes

`PINK_DEPLOYMENT_MODE` selects the settings module: `development` (default in the
dev containers), `staging`, `production`, or `build` (offline, for schema and
static generation during image builds).

## Settings and environment

Copy `.env.example` to `.env`. docker compose reads it automatically. The key
values:

- `PINK_OIDC_RP_CLIENT_ID` / `PINK_OIDC_RP_CLIENT_SECRET` - the confidential
  Authentik client for the Django builder/analytics site.
- `NUXT_PUBLIC_OIDC_CLIENT_ID` - the public (PKCE) Authentik client for the Nuxt
  runner.
- `PINK_OIDC_STAFF_GROUPS` - comma-separated Authentik groups granted staff
  access. Empty means no one is staff via OIDC; use the break-glass superuser.
- `PINK_SURVEYJS_LICENSE_KEY` - required in production for Creator and Analytics.

Production adds environment-driven `PINK_DJANGO_SECRET_KEY`, `PINK_ALLOWED_HOSTS`,
and `PINK_DB_*`; see `pink/settings/production.py`.

## Authentik setup

Register a `pink` application in Authentik (account.ietf.org). Pink uses two
clients against it:

- Confidential client (Django site). Register redirect URI
  `http://localhost:8088/oidc/callback/` for development, plus the staging and
  production equivalents.
- Public client with PKCE (Nuxt runner). Register redirect URI
  `http://localhost:8088/auth/callback` for development.

OIDC endpoints are derived from the host and application slug in
`pink/settings/base.py`; only credentials and redirect URIs need configuring.

## Break-glass superuser

For access when Authentik is unavailable, create a local superuser:

```
./manage.py createsuperuser
```

It can sign in at `/admin/`.

## Authentication summary

- Django `/manage/` builder and analytics: server-side OIDC login (session).
- Nuxt runner: browser OIDC via oidc-client-ts; required only for surveys whose
  visibility is `authenticated`.
- API: Pink validates Authentik bearer tokens as a resource server; the
  open-survey list treats a token as optional (it adds user-specific surveys
  when present) and popularity and open surveys are anonymous.

## The API contract

drf-spectacular is the source of truth. Regenerate and validate the schema:

```
PINK_DEPLOYMENT_MODE=build ./manage.py spectacular --file pink_api.yaml --validate
```

`pink_api.yaml` is committed. The Nuxt client generates TypeScript types from it
with openapi-typescript (the `gen:api` script, run on `npm install`); Red
generates its own client from the same file.

## SurveyJS bundles

The Django builder and analytics pages load self-hosted SurveyJS bundles, not a
CDN, so the Content Security Policy can stay strict. `vendor/` pins the packages
and `vendor/sync.sh` copies their dist files into the Django static tree. This
runs during container init and the statics image build. See `vendor/README.md`.

## Tests, lint, types

```
./manage.py test
ruff check .
ruff format --check .
cd client && npm run typecheck
```
