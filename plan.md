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
- Document sets, API only. Red has the UI for creating a set, titling and describing
  it, and adding documents to it; Reef stores the sets and their membership. A set is
  a subscribable thing, which is the reason it exists here rather than in Red's own
  storage.
- Subjects, API only. Reef hosts the subject vocabulary and decides which documents
  carry which subject; Red renders them on its RFC pages and offers them to subscribe
  to. Reef's own, not the datatracker's: the decision is that a subject is something
  the RFC series curates here rather than something read out of another system.
- Subscriptions and notifications, API only plus email delivery. Red has the
  subscribe UI; Reef stores subscriptions, ingests RFC-change events from
  datatracker, and sends notification emails.
- Per-document statistics, API only. Reef serves the rating aggregate, subscriber
  count and set count for a document; Red precomputes the whole series in one call
  at build time and renders the numbers on its RFC pages.

### Scope of this plan

Surveys are built end to end. Ratings, popularity, and subscriptions are scaffolded
as Django API app modules (models, endpoints, and test stubs) to be completed in a
later phase. Subscription email is built: reef.mail carries the project's mail
defaults, templates/subscriptions/mail holds the two message bodies and the sentence
they share, and both send on a retrying celery task. A confirmation goes out when a
subscription is created, which is the one of the two that is wired end to end. The
digest has no caller yet, because the datatracker change-feed remains scaffolded.

The confirmation is a courtesy rather than a verification: subscribers authenticate
through Authentik, so the address is already known good and nothing waits on the
message. Subscribe-by-bare-email, if it is ever built, needs a real verification
message and Subscription.verified starting False; that is a second message, not a
flag on this one.

Subjects are built: the vocabulary, the assignments that put a document under a
subject, the public read API, admin curation, and subscribing to a subject. They were
new scope, arriving with the decision to host the vocabulary in Reef rather than take
it from the datatracker, and they are the one part of the subscription story whose
matching does not wait on ingestion: because Reef owns the association, a subject
resolves to documents by a join here rather than by a tag arriving on an event.

Document sets are built: models, the owner-scoped API, the public read path, and
subscribing to a set. They were new scope rather than a gap in the scaffold, so they
still need a ticket of their own and a cross-repo agreement with Red on the UI, in
the way the open-survey list did. Matching a set to a change event is written and
tested, and sending the resulting digest now is too. What is still missing between
them is ingestion: nothing turns a datatracker feed record into an event, coalesces a
subscriber's matches, or answers the subseries question under open items.

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
                        +- /,/s/*  ------------------------------> Nuxt survey runner :3001
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
    docids.py              shared document-identifier parsing and canonical form
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
  docsets/                 DocumentSet and DocumentSetEntry, owner-scoped API, public read
  subjects/                Subject vocabulary and SubjectAssignment, public read API, admin curation
  subscriptions/           scaffold: Subscription model, API, email task, datatracker-feed ingest interface
  stats/                   per-document engagement numbers for Red; no models of its own
  vendor/                  package.json for self-hosted SurveyJS Creator/Analytics bundles into Django static
  client/                  Nuxt 4 survey runner (themed)
    nuxt.config.ts         ssr enabled, :3001; server-side API base for render-time fetches
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
- docsets.DocumentSet: owner (FK), title, description, deleted_at and
  deleted_reason, timestamps. docsets.DocumentSetEntry:
  set FK, doc (a canonical identifier), rank for display order, added_at, unique
  (set, doc). A set is a user's own list of documents, made in Red, and the unit a
  notification can be about.

  A set has no visibility, on the model or in the API: it is made to be shared, and
  holding its id is the whole of the permission to read it. The unguessable id is
  what keeps a set from being found by anyone who was not given the link. Private was
  in the first cut, as the owner's choice, and was then kept briefly as a staff
  unpublish state after the API stopped offering it; both are gone. Two staff states
  were one too many, and the leftover column stranded any set created under the old
  private-by-default: its owner could no longer publish it, because the API had no
  field to do it with, so a set of theirs was readable by nobody but them with no way
  out. Staff moderation is the soft delete alone. See the moderation open item.

  The id is the whole of a set's identity: there is no slug. A slug was in the first
  cut, as a readable second path segment with a redirect when it went stale, and it
  was dropped because nothing needed it. Nobody reads an API URL, and Red builds the
  URLs its readers do see, from a title it already has in the payload. What it cost
  was a derived column, a unique (owner, slug) constraint, a suffixing loop, a
  fallback for titles that slugify to nothing, and a redirect branch that had to be
  suppressed for taken-down sets so as not to confirm them. Two sets may now share a
  title, which is correct: they are two sets.

  A set holds series documents generally, not only RFCs, so DOC_SERIES grows to rfc,
  bcp, std and fyi. This is what the series-prefixed identifier form was for: bcp14
  and rfc14 are different documents and a set has to be able to hold both.

  Reef stores identifiers and almost nothing else about a document: no title, no
  status, no existence check. Red is the RFC website and already has that metadata, so
  a second copy here would only go stale. Identifiers are validated for syntax, as
  ratings and popularity already do.

  Subjects are the one exception, and they do not weaken the rule, because the rule
  was argued from staleness. A title copied here drifts from the datatracker's. A
  subject has no upstream to drift from: it is decided in Reef, so Reef is the only
  system holding an opinion for a second copy to disagree with. What follows from that
  is that Reef still cannot tell whether a document exists. A subject can be assigned
  to an identifier that names nothing, which is a curation error rather than something
  the database catches, exactly as a rating or a set entry naming a nonexistent RFC
  already is.
- Document identifiers, shared: ratings.Rating.rfc and popularity.PopularEntry.rfc
  store whatever string they are handed, while subscriptions canonicalizes through
  normalize_doc_id. Sets would be a third rule in a fourth place, and a set cannot be
  joined to its documents' ratings or subscriptions unless all of them agree. So
  normalize_doc_id, DOC_SERIES and the length bound move to reef/docids.py, and
  ratings and popularity adopt them with a data migration that backfills existing
  rows. Doing this before sets exist is the cheap moment; afterwards it is a
  four-table backfill.
- subjects.Subject: slug, name, description. The curated vocabulary, maintained by
  staff in the admin and served read-only, in the way popularity.PopularEntry is.
  A subject has two identities and needs both: the primary key is what a subscription
  points at, so that renaming a subject cannot detach its subscribers, and the slug is
  what a caller addresses it by and what Red puts in a URL its readers see.

  That is the opposite of the call made for document sets, which dropped their slug,
  and the difference is who names the thing. A set is titled by its owner, so titles
  collide, change often, and slugify to nothing often enough to need a fallback and a
  suffixing loop. A subject is named once by staff, and two subjects sharing a name is
  a curation mistake to prevent rather than a case to tolerate. The uniqueness that
  cost document sets too much is the point here.
- subjects.SubjectAssignment: subject FK plus a canonical document identifier, unique
  together. A row per pair rather than a list on either side, because this is the join
  a subscription match runs through and it has to be indexable from the document end,
  which is the end a change event arrives at. Unassigning is a hard delete; there is
  no state between assigned and not.
- subscriptions.Subscription (scaffold): user or email, kind (new_rfc, by_status,
  obsoleted, rfc, set, subject, and similar), params (JSON), verified flag. The
  rfc kind watches one RFC, identified by params {"rfc": "rfc9110"}; matching it
  against an event is an equality test on that id. Unique on
  (user, kind, params, set, subject).

  The kinds split in two. new_rfc, by_status and obsoleted are predicates over the
  event: they say what has to have happened, not which document it happened to, so no
  join resolves them and they belong to the ingest path. rfc, set and subject name
  documents and are matched by subscriptions_for_document.

  set and subject are the two that do not fit the params pattern, in the same two
  ways. Each points at a row, a DocumentSet or a Subject, so each takes a nullable FK
  on Subscription rather than an identifier in params: params has no referential
  integrity, and a title or slug in JSON would break on rename and leave silently dead
  subscriptions on delete. Both FKs join the uniqueness constraint, which puts three
  nullable identity columns in one constraint and needs nulls_distinct=False (see open
  items); each further relation makes that setting more load-bearing, not less. And
  each is matched by a join over membership that changes under the subscription,
  event to entries to sets and event to assignments to subjects, rather than by a test
  against the event alone. Exactly one relation column is filled for a subscription of
  that kind and all of them are null for every other kind, which is the shape the
  constraint is written against and which every write path checks.

  subject was drafted as a predicate, as subject_tag, back when a tag was going to
  arrive on the event from the datatracker. Hosting the vocabulary in Reef turned it
  into a join, which is why it is in the second group and not the first, and why it
  needs nothing from ingestion that rfc and set do not also need.

  Because the uniqueness constraint compares stored JSON, params has one canonical
  form, defined by subscriptions.models.normalize_params and applied in
  Subscription.save() as well as in the serializer, so the admin and the ingest task
  cannot write a shape the constraint would miss. The rules:

  - Each kind declares the keys it takes (Subscription.PARAMS_KEYS). Every declared
    key is required and any other key is rejected, rather than ignored: a stray key
    would make a duplicate subscription look distinct. new_rfc and obsoleted take no
    params at all.
  - Values are scalar strings, one subscription per value. Nothing takes a list,
    because JSON array order is significant and ["a","b"] would not equal ["b","a"].
  - Document identifiers carry their series, so "rfc9110" rather than "9110", which
    is what lets the subseries ("bcp14", "std66") join DOC_SERIES without any
    identifier becoming ambiguous. Input is accepted in the shapes people paste
    ("RFC 9110", "rfc-0791") and stored in one form; a bare number is rejected.
  - Other values are stripped and lowercased.

  Key order needs no normalizing: params is jsonb, which sorts keys, so the constraint
  already treats {"a":1,"b":2} and {"b":2,"a":1} as one value.

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
- GET/POST/DELETE /subscriptions/ (bearer) plus an internal ingest task hook. Kinds:
  new_rfc, by_status, obsoleted, rfc (one named RFC), set, and subject. POST is
  idempotent: a repeat returns 201 with the existing subscription rather than a
  duplicate, so Red's subscribe button needs no error branch for a double click, a
  resubmit, or a second tab. Tickets #126, #127, and #133.

Document sets (built, no ticket yet):

- GET/POST /sets/ (bearer, owner only) and PATCH/PUT/DELETE /sets/{id}/ (bearer,
  owner only): a user's own sets, with title, description and the document list.
  PATCH covers retitling and redescribing. There is no visibility field, because
  there is no visibility. The GET on /sets/{id}/ is the public read below.
- PUT/DELETE /sets/{id}/documents/{doc}/ (bearer, owner only): add or remove one
  document. PUT is idempotent for the same reason the subscribe POST is, and the
  identifier is canonicalized before it is stored, so /sets/3/documents/RFC%209110/
  and /sets/3/documents/rfc9110/ are the same entry.
- PUT /sets/{id}/order/ (bearer, owner only): reorder in one request. Ranks are
  rewritten as a block rather than patched per entry, so a drag-and-drop in Red is one
  call and cannot half-apply.
- GET /sets/{id}/ (anonymous): the same URL the owner reads, since the id is a set's
  whole identity and a shared link should not depend on who follows it. Every caller
  gets the same answer, which is the point: holding the id is the permission. A set
  staff have taken down 404s rather than 403s, for everyone alike, so nothing
  confirms it exists. The writes on this URL stay owner-only and 404 on someone
  else's set, so a refusal says nothing about whose it is. The owner is in neither
  the URL nor the response: the username is an opaque authentik-<sub> string, which
  is neither meaningful in a link nor something to publish, and a name in the body
  would attach a person to a reading list for anyone holding the link, which is more
  than the set itself says. A set is its title, description and documents.

Subjects (built, no ticket yet):

- GET /subjects/ (anonymous, unpaginated): the whole vocabulary, in name order, as
  id, slug, name and description. Unpaginated for the reason the popularity list is:
  it is curated rather than self-served, so it stays small enough to hand over whole.
  Public because a subject is public: it is rendered beside the document it describes,
  and a reader has to be able to see what they would be subscribing to before they
  have signed in.
- GET /subjects/?doc=rfc9110: the same list narrowed to the subjects one document
  carries, which is how Red renders the subjects on an RFC page without a second
  endpoint. Identifiers are canonicalized, so rfc9110 and RFC 9110 address the same
  document, and a bare number is rejected as everywhere else.
- GET /subjects/{slug}/ (anonymous): one subject and the documents carrying it.
  Addressed by slug because this is the path whose URL a reader sees; the response
  also carries the id, which is what subscribing names. Membership is here rather than
  on the list for the reason /me/ splits its set serializer: a picker needs every
  subject and no membership, so putting membership in the list would make the payload
  grow with the catalogue rather than with the vocabulary.
- No write path. Curation is staff work in the admin, both for the vocabulary and for
  assignments, so a POST is a 405 whoever is asking. If self-service assignment is
  ever wanted it is a new decision, not a missing endpoint.
- Subscribing to a subject is the subject kind on /subscriptions/, naming the subject
  by id. Unlike a set, it is not scoped to the caller: the vocabulary is public and
  has no owner, so every subject is subscribable by anyone and there is nothing for a
  queryset to hide.

Per-document statistics (built, no ticket yet):

- GET /stats/ (anonymous, unpaginated): a row per document, with rating_average,
  rating_count, subscriber_count and set_count. Red's build-time precompute fetches
  the whole thing in one call, which is why it is neither paginated nor keyed by a
  list of identifiers: naming the RFC series in a query string is thousands of
  parameters. Filtering with a repeated doc parameter is for one-off lookups, and a
  named document always comes back, with zeros if it has no engagement. Unfiltered,
  only documents that have some engagement appear.
- Filtering with set returns a row per document the set holds, including members with
  no engagement, which is what a set page in Red needs. It combines with doc as an
  intersection. Any set resolves for any caller holding its id, as on the set read
  itself, and an id that names no set 404s rather than 403s, a taken-down set
  included. The disclosure is the same either way: the rows list what the set holds,
  which is what following the link already shows.
- subscriber_count is distinct users across the three kinds that name a document: rfc,
  set and subject. The predicate kinds are excluded: new_rfc would otherwise add all of
  its subscribers to every recent RFC and flatten the number into noise. One user
  reaching one document through two of the counted kinds counts once. Subjects are the
  broadest term in the count, since one can cover far more documents than a hand-built
  set; see the subject-breadth open item.
- set_count counts distinct sets, minus the ones staff have taken down. The numbers
  are aggregate and name nobody: a count of one says somebody tracks this document,
  not who. A set staff have taken down is left out, along with subscriptions to it,
  because the counts would otherwise be the last place a deleted set still showed.
- Rows cover every series a set can hold, so bcp14 and std66 appear alongside RFCs
  even though only RFCs can currently be rated.

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

Then, for document sets:

15. Shared document identifiers: move normalize_doc_id and DOC_SERIES from
    subscriptions to reef/docids.py, add bcp, std and fyi, and backfill
    ratings.Rating.rfc and popularity.PopularEntry.rfc to the canonical form. This
    lands first: it is a two-table backfill now and a four-table one later. Commit:
    "Share document identifier normalization".
16. Document sets: docsets app with DocumentSet and DocumentSetEntry plus migrations,
    owner-scoped DRF endpoints, anonymous read by id, admin, tests. Export the
    schema. Commit: "Add user document sets".
17. Set subscriptions: the set kind, the nullable set FK in the uniqueness constraint,
    and join-based matching in ingest_rfc_change. Commit: "Add subscriptions to
    document sets".
18. Per-document statistics: the stats app, one anonymous unpaginated endpoint
    aggregating ratings, subscribers and sets for Red's build-time precompute.
    Commit: "Add per-document statistics API".

Then, for subjects:

19. Subjects: the subjects app with Subject and SubjectAssignment plus migrations, the
    public read API, admin curation, and the subject subscription kind: the nullable
    subject FK in the uniqueness constraint, replacing the free-text subject_tag kind,
    and join-based matching in subscriptions_for_document. The old subject_tag rows are
    dropped rather than carried across: resolving one would mean creating a Subject for
    whatever string was typed, which is how a curated vocabulary acquires "secuirty" on
    its first day, and no subject_tag subscription ever produced a notification, since
    the kind was matched on the ingest path and that is a stub. Export the schema.
    Commit: "Add subjects and subject subscriptions".

## Verification

- Dev bring-up: devcontainer (or docker/run); migrate and collectstatic run; tmux
  shows Django (:8001), Nuxt (:3001), nginx (:8088), and celery. http://localhost:8088/
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
  subscription and enqueues a confirmation caught by mailpit, and a second POST of the
  same subscription does not send a second one.
- Document sets: a set is created with a title and description, an RFC and a BCP are
  added and reordered, a second add of the same document in another spelling does not
  duplicate it, a taken-down set is invisible to every GET, and a subscription to
  the set is matched by a change to a document added after it was made, and a batch of
  three changes to a subscribed set produces one mail naming all three rather than one
  mail per document. All covered by manage.py test. Not yet verifiable end to end:
  that a real datatracker change arrives as such a batch, because ingestion is still a
  stub.
- Subjects: GET /subjects/ with no token returns the vocabulary and no membership;
  ?doc= narrows it to one document's subjects and canonicalizes the identifier;
  GET /subjects/{slug}/ returns the documents carrying it; a POST is a 405. A
  subscription to a subject is matched by a change to a document assigned to it
  afterwards, stops matching when the assignment is removed, survives the subject
  being renamed, and goes away with the subject being deleted. A subscription naming
  both a set and a subject is refused. All covered by manage.py test.
- Statistics: GET /stats/ with no token returns a row per engaged document; adding a
  rating, a subscription and a set entry for one document moves all three numbers; a
  user subscribed both directly and through a set counts once; ?set= returns the
  members of a set to any caller holding its id, and 404s for an id that names none.
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
  scaffolded here; full wiring is a follow-up phase. Two things to confirm as it
  lands: the param key for by_status ("status") is named ahead of its matching logic,
  since the uniqueness constraint needs every kind to have a settled shape; and
  unsubscribe is a hard delete, so the constraint is unconditional. If a soft
  unsubscribed_at or a pending-verification state is ever added, the constraint has to
  become partial or it will block resubscribing. This item used to cover subject_tag's
  "tag" key as well. Hosting the vocabulary here settled that one by removing it: a
  subject is a relation now, so there is no free-text key left to agree.
- Email subscriptions: the data model above says "user or email" but Subscription is
  user-only. Adding an email column puts a fourth nullable identity column in the
  uniqueness constraint, alongside user, the set FK and the subject FK. Postgres counts
  NULLs as distinct, so this needs nulls_distinct=False (Django 5.0+, PG15+), which is
  already set and already carrying two relations. Adding a third is not a new risk but
  it is more weight on one setting, and the failure if it is ever lost is silent
  duplicate subscriptions rather than an error.
- Subseries in a subscribable set: a BCP or STD is a container whose membership
  changes (BCP 14 is currently RFC 2119 plus RFC 8174). "Updates to bcp14" therefore
  means two different things: a change to a constituent RFC, and a change to which
  RFCs constitute it. Decide whether ingest expands a subseries to its RFCs at match
  time, treats a constitution change as its own event, or both. Reef holds no document
  metadata, so whichever way it goes, the expansion has to come from the datatracker
  feed rather than from Reef's own tables. This is the substantive unknown in the sets
  proposal.
- Retiring and merging subjects: a curated vocabulary changes, and neither change has
  an answer yet. Deleting a subject cascades its subscriptions away, which is right for
  a mistake made this morning and wrong for a subject a thousand people follow that is
  being renamed into another. Merging is the case with no mechanism at all: there is no
  way to say "security is now part of security and privacy" and move both assignments
  and subscribers. The cheap first move is a retired flag that hides a subject from the
  picker and from new subscriptions while leaving existing ones matching; the real one
  is a merge that rewrites the FK. Neither is built. Until then the admin is a sharp
  tool: deleting a subject silently unsubscribes people who never asked to be.
- Assigning subjects at scale: the vocabulary is small and staff can type it, but the
  back catalogue is roughly 9,500 RFCs and the admin assigns one document at a time.
  Nothing decides whether subjects apply only to documents published from now on, or
  whether the catalogue gets backfilled, and if so from what. The point of hosting the
  vocabulary here was not to depend on the datatracker for it, so a bulk import has no
  obvious source. This is the practical unknown in subjects, in the way subseries is
  the substantive one in sets.
- Assignment as an event: "changes to anything on the subject of X" is ambiguous in the
  same way the subseries question is. A subscriber could mean a change to a document
  carrying X, which is what is built, or a document being newly given X, which is not.
  The second is an event no feed can supply: it happens when staff assign a subject in
  Reef's own admin, which would make Reef a source of change events rather than only a
  consumer of them. Everything in the notification path assumes the other direction.
  Wiring it means enqueuing a digest from the assignment save path, and deciding
  whether a subscriber wants to hear about a five-year-old RFC because it has just been
  categorized.
- Subject breadth in the statistics: subscriber_count now includes subject
  subscriptions, on the same reasoning that includes set ones: they produce mail about
  the document, so somebody is watching it. But a subject can cover a large fraction of
  the series, where a hand-built set is tens of documents, so a broad subject with many
  subscribers moves every one of its documents at once. That is not wrong, but it is
  the term most likely to make the number read as noise, and it is the one to revisit
  if it does. Excluding it is a one-line change in stats._subscriber_counts.
- Subjects on the Red side: no contract is agreed yet. Red needs the vocabulary for a
  picker, a document's subjects for its RFC pages, and a link target for a subject,
  which is why the detail read is addressed by slug. Whether Red wants a document count
  per subject in the list, and whether a subject should have a page of its own in Red
  or only a filter, are its calls. This is the same cross-repo agreement the open/ list
  (#225) and sets still need, and subjects need a ticket of their own alongside them.
- Subscribing to someone else's set: the first cut restricts subscriptions to your
  own sets. Opening it up means deciding what happens when the owner empties or
  deletes it. Continuing to mail a subscriber about a set that is no longer there
  leaks its membership, so the set has to be rechecked at send time and not only at
  subscribe time. The send path already does this for one case:
  a subscription whose set has been soft-deleted sends nothing, since a real delete
  would have cascaded the subscription away.
- Notification volume: half of this is now settled and half is not. The shape is
  settled, ahead of the templates as intended: delivery is
  send_subscription_digest(subscription_id, events), one call is one mail, and a batch
  of changes to a set arrives as one digest. What is not settled is who batches. A
  subscriber holding both a set and an overlapping rfc subscription still gets two
  mails, because the task sees one subscription and cannot know about the other, so
  coalescing per subscriber over a window and deduplicating per user per event have to
  happen in ingest_rfc_change before it enqueues anything. That is the remaining work
  here, and it is already reachable today with obsoleted plus rfc.
- One-click unsubscribe: notifications carry List-Unsubscribe pointing at
  REEF_SUBSCRIPTIONS_URL, but not List-Unsubscribe-Post, because RFC 8058 one-click
  needs an unauthenticated tokenized endpoint to POST to and Reef's unsubscribe is an
  authenticated delete. Mailbox providers increasingly weight one-click support, so
  decide whether to add a signed-token endpoint. It is the same token machinery the
  unbuilt subscribe-by-bare-email case needs, so decide the two together.
- Sets are user-generated content on an ietf.org origin: a title and description are
  free text, shareable, and attributable to an IETF account. Staff need a way to take
  one down without deleting a user's data, and that is the soft delete: deleted_at
  with an optional deleted_reason, set from the admin, absent from the API so the
  owner cannot undo it. Still to decide: whether the admin is enough or this needs a
  real report-and-review path; whether a set's owner should be told when one of
  theirs is taken down; and whether a middle state is ever wanted, hidden from
  readers but still the owner's to edit. There was one, and it was removed as an
  overlapping second staff state rather than because the idea is wrong — if it comes
  back it should come back as an explicit staff hold, not as an owner-facing
  visibility field.
- Shareable sets also bring the cross-user subscription question forward: a set is
  exactly the thing another person would want to subscribe to, which makes the
  own-sets-only restriction in the first cut a stopgap rather than a settled position.
- Statistics freshness and cost: /stats/ aggregates on every request, which is fine
  for a build-time caller and wrong for a hot public endpoint. If Red ever fetches it
  per page view, or the tables grow, this wants caching or a materialized counter
  rather than a live aggregate. Decide when Red's usage is known, not before.
- Nuxt OIDC client registration: confirm a public (PKCE) Authentik client for the runner
  versus reusing Red's client configuration.
- Survey targeting: the audience and user-specific-offer rules (subscription-driven)
  need a concrete specification beyond the scaffold.
```
