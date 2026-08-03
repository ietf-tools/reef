# Reef: Survey and Engagement Service Implementation Plan

## Context

Reef is an IETF-tools project in the "RFC Modernization Phase 2" program, alongside
Red (the public RFC website). Reef is the self-hosted survey and engagement service.
It self-hosts SurveyJS (rather than the SurveyJS hosted service) plus a set of
engagement APIs consumed by Red.

Reef follows the conventions of Purple (https://github.com/ietf-tools/purple):
Django 5 with Django REST Framework and PostgreSQL, Docker and devcontainers,
dev/staging/prod modes, the docker/ plus dev/build/ plus k8s/ layout, an NGINX front
proxy, and OIDC authentication against the IETF Authentik instance at
https://account.ietf.org/.

### Responsibilities

- Surveys, full stack in Reef:
  - The Django site hosts the SurveyJS Creator (builder) and Analytics (dashboard).
    Login is required (staff and authors).
  - The Reef Nuxt site hosts the themed survey runner where visitors fill out
    surveys. It supports both anonymous and logged-in surveys.
  - Red calls a Reef API for open surveys, then shows a popover linking to the
    survey on the Reef Nuxt runner. Red renders no survey UI.
- Ratings, API only. Red renders the star widget; Reef stores and aggregates.
- Popularity, API only. Reef serves a curated "most popular" list; Red consumes it.
- Subscriptions and notifications, API only plus email delivery. Red has the
  subscribe UI; Reef stores subscriptions, ingests RFC-change events from
  datatracker, and sends notification emails.

### Scope of this plan

Surveys are built end to end. Ratings, popularity, and subscriptions are scaffolded
as Django API app modules (models, endpoints, and test stubs) to be completed in a
later phase. Subscription email delivery and the datatracker change-feed are
scaffolded as an interface and an async task rather than fully wired.

### Ticket alignment

The Zenhub tickets match this plan. Tickets #119, #120, and #122 read "Build front
end at Reef for managing surveys / viewing survey results / displaying survey to user
and capturing results", and #113 reads "Provide an API to Reef for storing survey
results". Mapping: #119 to the Django builder, #120 to the Django analytics, #122 to
the Nuxt runner, #113 and #116 to the results-storage API consumed by the Reef Nuxt
runner, #112 and #115 to the open/offer list queried by Red, #111 to the manage API,
and #118 to the view-results API. Ticket #225 covers Red's survey popover and link-out
(link-out only; Red never authors or captures), and depends on the Reef open-surveys
API shape.

### Prerequisites (not code tasks)

- A SurveyJS commercial license for Creator and Analytics in production. The runner
  and storage are MIT. Development works unlicensed with a watermark.
- A `reef` OIDC application registered in Authentik (account.ietf.org): one
  confidential client for the Django site (server-side code flow) and one public
  client for the Nuxt runner (PKCE), with dev/staging/prod redirect URIs registered.

## Architecture

One NGINX origin per environment fronts three processes. The Django session cookie
covers the builder and analytics site; the Nuxt runner and all APIs use OIDC bearer
tokens.

```
                         +----------------- Reef -----------------+
Browser -> NGINX :8088 -+- /manage,/admin,/oidc,/api,/static -> Django + DRF :8001 -> PostgreSQL
                        +- /,/s/*  ------------------------------> Nuxt survey runner :3000
Red  --------------------  GET /api/reef/... (bearer / anon) ---> Django + DRF
All logins ----------------------------------------------------> Authentik (account.ietf.org)
Celery worker: subscription emails <- datatracker RFC-change feed
```

- Django site (builder and analytics): server-rendered template pages that mount the
  vanilla SurveyJS bundles (survey-creator-js, survey-analytics), self-hosted, no CDN.
  Login uses mozilla_django_oidc. The /manage/ paths require login.
- Nuxt runner (client/, SSR enabled): themed survey pages (survey-vue3-ui plus a
  per-survey theme JSON). Browser OIDC uses oidc-client-ts (PKCE), the same library
  Red uses. Protected surveys require login; open surveys are anonymous.
- DRF APIs (/api/reef/): Reef acts as an OIDC resource server, validating Authentik
  bearer (JWT) access tokens. Anonymous access is allowed for public reads.
- Break-glass: one local Django superuser for admin access if Authentik is
  unavailable.
- Async: Celery plus a broker for subscription emails (scaffolded in this phase).

### Authentication model (single IdP: Authentik at account.ietf.org)

| Surface | Mechanism |
|---|---|
| Django /manage builder and analytics | mozilla_django_oidc code flow to a Django session; login required |
| Django /admin fallback | local superuser (break-glass) |
| Nuxt survey runner | oidc-client-ts (Auth Code plus PKCE) to an access token; required only for protected surveys |
| Reef DRF APIs | resource server: validate Authentik bearer JWT. Optional on the open-survey list (adds user-specific surveys when present), required for rating submit and subscriptions, anonymous for popularity and open surveys |

### Repository layout (Purple-shaped)

```
reef/
  .devcontainer/           service "app", forwards :8088; init/start hooks and extend overlays
  .github/workflows/       build.yml, build-base-app.yml, test.yml
  docker/                  dev env: base/app/db.Dockerfile, configs/nginx-proxy.conf,
                             scripts/app-*.sh (tmux: runserver, nuxt, nginx, celery), run, cleanall
  docker-compose.yml       db, app, mailpit, pgadmin, mq (rabbitmq), celery
  dev/build/               prod images: backend.Dockerfile, frontend.Dockerfile,
                             statics.Dockerfile, celery, gunicorn.conf.py, *-start.sh
  manage.py  pyproject.toml  requirements.txt
  reef/                    Django project
    settings/{__init__,base,development,staging,production,build}.py
    settings/logging/{development,production}.py
    celery.py  urls.py  wsgi.py  openapi.py
  reefauth/                OIDC RP login (mozilla_django_oidc) plus DRF bearer resource-server
                             auth plus custom User: models, backends, authentication, apps, utils, migrations
  surveys/                 full build
    models.py              Survey, Response
    admin.py               break-glass listing and inspection
    serializers.py  api.py DRF endpoints (manage, open list, runner fetch, submit, results)
    views.py  urls.py      /manage/ builder and analytics template views
    templates/surveys/{creator,analytics,list}.html
    static/surveys/{init-creator,init-analytics}.js
    rules.py  factories.py  tests.py  migrations/
  ratings/                 scaffold: Rating model, aggregate/submit API, test stub
  popularity/              scaffold: curated-list model/config, read API, test stub
  subscriptions/           scaffold: Subscription model, API, email task, datatracker-feed ingest interface
  vendor/                  package.json for self-hosted SurveyJS Creator/Analytics bundles into Django static
  client/                  Nuxt 4 survey runner (themed)
    nuxt.config.ts         ssr enabled, :3000; server-side API base for render-time fetches
    app/pages/{index,s/[slug]}.vue
    app/components/SurveyRunner.client.vue
    app/composables/{useOidc,useSurveyApi}.ts
    app/assets/theme/
  k8s/                     base plus overlays/{staging,production}
  docs/  README.md
```

### Data model

- surveys.Survey: slug (unique), title, description, definition (JSON, SurveyJS
  survey JSON), theme (JSON, nullable), status (draft/published/closed), visibility
  (open/authenticated), audience (JSON, nullable, targeting rules such as subject
  subscriptions for user-specific offers), created_by, timestamps.
- surveys.Response: survey FK, data (JSON), submitted_by (FK, nullable for anonymous),
  submitted_at, meta (JSON).
- ratings.Rating (scaffold): rfc (identifier), user, value (1-5), unique (rfc, user),
  timestamps. Aggregate is average plus count per rfc.
- popularity (scaffold): a curated ordered list of RFC ids (manually managed JSON,
  per ticket #1), served read-only.
- subscriptions.Subscription (scaffold): user or email, kind (new_rfc, by_status,
  obsoleted, subject_tag, and similar), params (JSON), verified flag.

### API surface (/api/reef/, DRF plus drf-spectacular)

Surveys (built):

- GET/POST /surveys/, GET/PUT/DELETE /surveys/{id}/: management (session or bearer,
  staff). Used by the builder.
- GET /surveys/open/: open-survey list for Red (tickets #112 and #115; consumed by the
  Red popover and modal, #225). Bearer optional: include user-specific surveys (for
  example from subscriptions) when a user is identified, otherwise open/anonymous only.
  Returns per survey: id, slug, title, description, url (link to the runner on the Reef
  Nuxt site). Stateless with respect to taken/dismissed state: Red tracks that
  client-side in localStorage; Reef returns the full targeted list. The shape must be
  agreed with #225.
- GET /surveys/{slug}/definition/: definition plus theme for the Nuxt runner (bearer
  required only when visibility is authenticated).
- POST /surveys/{slug}/responses/: submit (bearer required only for authenticated
  surveys). Tickets #113 and #116.
- GET /surveys/{id}/results/: aggregated results feeding the Analytics dashboard.
  Ticket #118.

Scaffolded (models and endpoints stubbed, returning minimal real data):

- GET /ratings/{rfc}/ (anonymous aggregate), PUT /ratings/{rfc}/ (bearer). Ticket #108.
- GET /popularity/ (anonymous curated list). Tickets #101 and #102.
- GET/POST/DELETE /subscriptions/ (bearer) plus an internal ingest task hook. Tickets
  #126, #127, and #133.

### API contract

drf-spectacular is the single source of truth for the API contract. It serves
/api/reef/schema/ and Swagger at runtime, and manage.py spectacular exports the
schema to reef_api.yaml, which is committed as the published contract. Both
consumers work from that schema rather than hand-agreed shapes:

- Reef's Nuxt runner generates TypeScript types from reef_api.yaml with
  openapi-typescript (pure npm, no Java toolchain) and calls the API with typed
  fetch helpers.
- Red consumes the same published reef_api.yaml to generate its own client
  independently in its repository.

This keeps the /api/reef/surveys/open/ contract with Red (#225) and the runner
contract machine-checkable on both sides. The heavier openapi-generator client
used by Purple is intentionally avoided so the base image needs no Java.

### dev / staging / prod modes

REEF_DEPLOYMENT_MODE (development, staging, production, build) selects the settings
module. staging.py imports from production.py and adds staging hosts. build.py is
offline-only for schema and static generation at image build time.

## Implementation steps

Each step ends with a commit.

1. Django scaffold: pyproject.toml (ruff), requirements.txt (Django, DRF,
   drf-spectacular, mozilla-django-oidc, PyJWT/jwcrypto for bearer validation, psycopg,
   celery, rules), manage.py, reef/. Commit: "Scaffold Django project".
2. Settings package and modes: reef/settings/* plus logging plus celery.py. Commit:
   "Add settings package with deployment modes".
3. Dev Docker and compose: docker/{base,app,db}.Dockerfile, configs/nginx-proxy.conf
   (8088 to Django for /manage, /admin, /oidc, /api, /static; to Nuxt for / and /s/*),
   scripts/app-*.sh (tmux: runserver, nuxt, nginx, celery), run and cleanall,
   docker-compose.yml (db, app, mailpit, pgadmin, mq, celery). Commit: "Add Docker dev
   environment".
4. Devcontainer: .devcontainer/ mirroring Purple. Commit: "Add VS Code devcontainer".
5. Auth (reefauth): custom User; mozilla_django_oidc RP login (Django site) to
   Authentik; DRF authentication.py validating Authentik bearer JWTs (resource server)
   with optional-auth support; break-glass superuser; /oidc/ urls. Commit: "Add
   Authentik OIDC login and bearer resource-server auth".
6. Surveys models and API: Survey and Response plus migrations, serializers, DRF
   endpoints (manage, open/, definition/, responses/, results/), rules.py,
   drf-spectacular. Export the schema to reef_api.yaml and commit it as the
   published contract. Commit: "Add surveys models and REST API".
7. Vendored SurveyJS bundles: vendor/package.json (survey-core, survey-js-ui,
   survey-creator-core/js, survey-analytics plus plotly) into Django static plus
   collectstatic; strict CSP, self only. Commit: "Vendor self-hosted SurveyJS bundles".
8. Django builder: /manage/surveys/ list plus create/edit, creator.html plus
   init-creator.js mounting survey-creator-js, saveSurveyFunc to the API, login-guarded,
   license key injected. Commit: "Add Django-hosted survey builder".
9. Django analytics: /manage/surveys/{id}/analytics/, analytics.html plus
   init-analytics.js mounting VisualizationPanel over results/. Commit: "Add
   Django-hosted survey analytics".
10. Nuxt runner and OIDC: client/ Nuxt 4 SPA, useOidc.ts (oidc-client-ts, PKCE),
    s/[slug].vue, SurveyRunner.client.vue (survey-vue3-ui plus theme). Generate TS
    types from reef_api.yaml with openapi-typescript and call the API with typed
    fetch helpers in useSurveyApi.ts (bearer when logged in); submit responses.
    Commit: "Add themed Nuxt survey runner with OIDC".
11. Scaffold ratings, popularity, subscriptions: three Django apps with models plus
    migrations plus DRF endpoints returning minimal real data; a celery task and a
    datatracker-change ingest interface for subscription emails (not fully wired); test
    stubs. Commit: "Scaffold ratings, popularity, and subscriptions APIs".
12. Prod images and CI: dev/build/{backend,frontend,statics}.Dockerfile, celery image,
    gunicorn.conf.py, start scripts, .github/workflows/{build,build-base-app,test}.yml.
    Commit: "Add production image builds and CI".
13. k8s manifests: k8s/base (django, nginx, frontend, celery, mq/memcached as needed,
    service) plus overlays/{staging,production}. Commit: "Add k8s kustomize manifests".
14. Docs: README.md dev quickstart, docs/, .env.example. Commit: "Add README and
    developer docs".

## Verification

- Dev bring-up: devcontainer (or docker/run); migrate and collectstatic run; tmux
  shows Django (:8001), Nuxt (:3000), nginx (:8088), and celery. http://localhost:8088/
  serves the runner; /api/reef/schema/ responds; mailpit catches mail.
- Auth:
  - /manage/ redirects to OIDC login at account.ietf.org and returns a session;
    unauthorized users are blocked; the break-glass superuser works at /admin/ when
    Authentik is unavailable.
  - Nuxt: an open survey loads anonymously; a survey with visibility authenticated
    triggers oidc-client-ts login before rendering.
  - GET /api/reef/surveys/open/ with no token returns open surveys only; with a user
    bearer it also returns that user's targeted surveys.
- Core survey flow (author, offer, fill, analyze):
  1. /manage/surveys/new/ builds and saves a survey in the embedded Creator; publish,
     set theme and visibility.
  2. GET /surveys/open/ returns it; a visitor opens the popover link to the Reef Nuxt
     /s/<slug> runner and submits.
  3. A Response row is stored (POST .../responses/ returns 201) and is visible in
     /admin/.
  4. /manage/surveys/<id>/analytics/ renders the results.
- Scaffolds: GET /popularity/ returns the curated list; PUT /ratings/{rfc}/ with a
  bearer stores a rating and the aggregate updates; POST /subscriptions/ stores a
  subscription and enqueues a confirmation caught by mailpit.
- Checks: ruff check and manage.py test; manage.py spectacular --validate; npm run lint
  and typecheck in client/.
- Modes: build images; run with REEF_DEPLOYMENT_MODE=production and real env;
  Creator and Analytics load without a watermark; CSP serves only self-hosted bundles.
- k8s: kubectl kustomize k8s/overlays/staging renders and dry-run applies.

## Open items

- open/ API contract with Red (#225): agree the exact response shape (id, slug, title,
  description, url) and user-targeting semantics. Red owns taken/dismissed tracking, so
  Reef stays stateless there. This is a cross-repo dependency.
- Subscriptions depth: datatracker change-feed ingestion (#139) and email templates are
  scaffolded here; full wiring is a follow-up phase.
- Nuxt OIDC client registration: confirm a public (PKCE) Authentik client for the runner
  versus reusing Red's client configuration.
- Survey targeting: the audience and user-specific-offer rules (subscription-driven)
  need a concrete specification beyond the scaffold.
```
