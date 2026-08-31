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
- Subscriptions and notifications, API only plus email delivery. Red has the subscribe
  UI; Reef stores subscriptions, detects RFC changes by diffing Red's published index,
  and sends notification emails. Not from the datatracker: it publishes no change feed,
  no events endpoint and no webhook, so there is nothing to subscribe to. See the
  detection step below.
- Per-document statistics, API only. Reef serves the rating aggregate, subscriber
  count and set count for a document; Red precomputes the whole series in one call
  at build time and renders the numbers on its RFC pages.
- Precomputed reads, written rather than served. Every anonymous public read above is
  also rendered ahead of time to a blob store, so that a caller can be handed a file
  instead of a query that aggregates every rating, subscription and set entry. Reef
  still serves all of them live: the files are the fast path, not a replacement.
- Document metadata, read but never stored. Reef resolves an identifier to a title or
  a subseries' contents from Red's public files when it needs to show one. It holds no
  copy; see the data model for why that distinction carries the weight it does.

### Scope of this plan

Surveys are built end to end. Ratings, popularity, and subscriptions are scaffolded
as Django API app modules (models, endpoints, and test stubs) to be completed in a
later phase. Subscription email is built: reef.mail carries the project's mail
defaults, templates/subscriptions/mail holds the two message bodies and the sentence
they share, and both send on a retrying celery task. A confirmation goes out when a
subscription is created, which is the one of the two that is wired end to end. The
digest is sent by the daily change run; see the detection steps below.

The confirmation is a courtesy rather than a verification, and no verification exists
anywhere in Reef, because none is needed: every subscriber authenticates through
Authentik and the address is the one on that account, which account.ietf.org has already
verified. Reef never accepts an address typed into a form, so there is nothing for it to
prove. Subscription.verified, which came from a design where it might have been, is
removed: it defaulted True, nothing ever set it otherwise and nothing read it. Removing
it drops a field from the published contract, so Red drops the "awaiting email
verification" line that read it.

Subjects are built: the vocabulary, the assignments that put a document under a
subject, the public read API, admin curation, and subscribing to a subject. They were
new scope, arriving with the decision to host the vocabulary in Reef rather than take
it from the datatracker, and they are the one part of the subscription story whose
matching does not wait on ingestion: because Reef owns the association, a subject
resolves to documents by a join here rather than by a tag arriving on an event.

The precomputer is built: one management command renders every anonymous public read
to a blob store, S3 where a bucket is configured and a local directory where none is.
It is the counterpart of Red's precomputer, which has five entry points differing only
in what they select and how they report; both are arguments rather than programs, so
this is one command.

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
Red  <-- precomputed JSON <---- blob store <---- manage.py precompute (scheduled)
All logins ----------------------------------------------------> Authentik (account.ietf.org)
Celery worker: subscription emails <- daily diff of Red's published index
Document titles <- GET www.rfc-editor.org/api/v1/... (anonymous, no key)
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
- Precomputer: a management command, run by celery beat rather than a process of its
  own. It renders the public reads by
  calling the DRF views in process, so a precomputed file cannot describe a different
  shape from the live response, and then adds resolved document metadata to whatever
  names a document. A file is meant to serve one of Red's routes in one fetch, so it
  carries what the route renders rather than identifiers the caller must resolve
  elsewhere. Additions are new keys only, so every file stays the live response plus
  zero or more of them. Run on a schedule; nothing serves traffic from it inside Reef.
- Document metadata: reef/rfcmeta.py, an anonymous HTTP read of Red's public files,
  validated against a JSON Schema generated from Red's Zod definition and synced into
  reef/schemas/. No credential, no generated client, and no row written anywhere in
  Reef.

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
    locks.py               postgres advisory lock, so two runs of a job cannot overlap
    rfcmeta.py             titles and subseries contents, read from Red's public files
    admin_documents.py     the title column the admin shows beside an identifier
    schemas/               JSON Schema for data Reef reads from elsewhere; synced copies
      rfc-mini-index.schema.json  generated from Red's RfcMiniSchema, synced by hand
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
  precomputer/             renders the public reads to a blob store; one management command
    blobstore.py           S3 when a bucket is configured, a local directory when not
    render.py              runs a DRF view in process and returns the bytes it served
    registry.py            one job per public read, each owning the keys it may purge
    tasks.py               celery tasks that run the command on a schedule

    signals.py             a staff edit to curated content enqueues a refresh
    management/commands/precompute.py   the single entry point
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

  Reading that metadata is a different act from storing it, and Reef now does the first
  without the second. reef/rfcmeta.py resolves an identifier against Red's public files:
  /api/v1/rfc-mini-index.json for the whole series in one response, which is where the
  precomputer's per-run sweep gets title and subseries membership;
  /api/v1/rfc-common/{n}.json for one document, which is fuller and is what an admin
  page or a confirmation email reads; and /api/v1/info-subseries/{type}{n}.json for a
  container's contents. All three are anonymous, so there is no key to hold and no
  client to generate. The rule above survives intact, because the argument for it was
  staleness and nothing here is written to a column: a precomputer run fetches the index
  once and holds it for that run, and an admin page or a confirmation email fetches one
  document. Existence checking becomes possible on the same read, which is what would
  close the curation gap in the paragraph above; whether to enforce it at write time is
  not decided.

  The datatracker was the other candidate and was not chosen. Its /api/red/ tag serves
  the same metadata, and Purple already consumes the neighbouring /api/purple/ tag
  through a generated client, so the machinery is proven. What it costs is an
  openapi-generator step in the image, a generated package installed from a path in
  requirements.txt, an API key per deployment, and somewhere to cache: production and
  staging get memcached when the k8s environment supplies MEMCACHED_SERVICE_HOST, but
  base and development are DummyCache, so a per-document client would re-fetch on every
  lookup in development and behave differently there from production. Red's files need
  none of that. The objection to reading Red is that Red reads Reef's precomputed
  bucket, so this closes a loop — but it is a loop of static-file reads, where neither
  side calls the other synchronously and the worst case is one run's lag, which is what
  the datatracker route would give too. What it does add is a second hop of staleness
  and a dependency on Red's precomputer succeeding; see the open item on stale reads.

  Red's side guarantees those files change additively: new keys may appear, existing
  ones do not change meaning or go away. That is what makes an artifact shaped for
  somebody else's index table safe to depend on, and it answers what would otherwise
  be the strongest objection to reading Red rather than the datatracker — that Reef
  would be building on a private shape with no promise attached. The guarantee binds
  Reef too, in a way worth being explicit about, because a consumer that rejects
  unknown keys turns every additive change into a break: rfcmeta reads the fields it
  needs and ignores the rest, and must not later acquire a strict schema that forbids
  extras. What the guarantee does not cover is the file being published at all, or
  Red's contributors knowing the guarantee exists; both are open items.

  One consequence is worth stating plainly, because it looks like the rule above being
  bent. The precomputed files carry document metadata, so a title does sit in something
  Reef writes. It is not a second copy in the sense the rule forbids: nothing is in a
  column, nothing is read back as truth, and every value is replaced wholesale on the
  next run. What it is instead is a cache with a schedule, and Red has accepted the
  drift that comes with one, because a route that fetches one file beats a route that
  fetches a list of identifiers and then resolves them. The rule holds where it was
  aimed, which is Reef's own tables.
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

Then, for precomputed reads:

20. Precomputer: the precomputer app and manage.py precompute, its single entry point.
    One task per anonymous public read — stats, popularity, subjects and each subject,
    the open-survey list and each open survey's definition, and each rated document —
    rendered by calling the DRF view in process so the file and the live response cannot
    disagree. Selection by task name, --doc to narrow the per-document tasks, --dry-run,
    --no-purge and --callback-url. Writes to S3 when a bucket is configured and to a
    directory when none is, chosen by configuration alone so a deployment cannot be
    argued into writing production payloads inside its own container. Production goes
    further and sets REEF_PRECOMPUTE_REQUIRE_S3, so an unconfigured deployment refuses
    rather than falling back: a scheduled worker writing into its own filesystem would
    log a successful run every hour and publish nothing, which is the failure that looks
    healthiest. The bucket and endpoint are in the k8s configmap and the credentials in
    the secret, both empty until an environment supplies them. Each task declares the
    keys it owns, so a full run purges what it no longer produces and leaves anything
    else in the bucket alone; the purge is skipped after a failed task and under --doc,
    where a missing key may be one this run did not rebuild. Excluded on purpose:
    me/documents/ and subscriptions/ are per-caller, surveys/ and results/ are
    staff-only, and sets/{id}/ reads anonymously only because holding the unguessable id
    is the permission, which does not survive a store whose keys can be listed. Commit:
    "Add API response precomputer".
21. Document metadata: reef/rfcmeta.py, resolving an identifier to a small metadata
    object (title, and whatever else the field-set item settles), and a subseries to its
    contents, from Red's public files over anonymous HTTP. It validates what it fetches
    against reef/schemas/rfc-mini-index.schema.json, a JSON Schema generated from Red's
    RfcMiniSchema by `npm run generate:schema` in Red's precomputer and synced here by
    hand. JSON Schema is the interchange because the shape is defined in Zod and Reef is
    Python, and it is exported with Zod's `io: 'input'` so that it carries no
    `additionalProperties: false`: Red changes these files additively, so a schema that
    rejected unseen keys would turn every field Red adds into a Reef outage. Removing a
    required field or retyping one fails; adding one passes. A failure is logged naming
    the field and the run continues with null metadata, because Reef's own payloads do
    not depend on Red. Validate the index once per run rather than per lookup; it is
    9,800 entries and about two seconds. Callers to follow it, in the order they are
    worth doing: titles beside the bare identifiers in the subjects, popularity and
    document-set admin, which is where staff curate against 9,800 documents they
    currently see only as numbers; the subscription confirmation email, which names a
    document at a moment when no change event exists to carry a title; and subseries
    expansion for set and subscription matching. What the precomputer's own output
    should carry is settled in the next step, not here. Adds jsonschema to requirements.
    Commit: "Resolve document titles from Red's published files".
22. Document metadata in the precomputed output: every precomputed file that names a
    document carries that document's metadata, so stats.json and popularity.json and
    subjects/{slug}.json and ratings/{doc}.json all do. The reason is Red's, not Reef's:
    an SPA route wants one resource, and a page that has to fetch a list of identifiers
    and then resolve them loads slower than one that fetches a file it can render. So
    the rule is that a precomputed file serves a route in a single fetch, and an
    identifier a caller would have to look up somewhere else is a fetch this was
    supposed to save. The earlier reasoning here was that titles in stats.json would
    send Red its own data back; that weighed a duplicated byte against a round trip,
    which is the wrong comparison. Drift is accepted explicitly: a title in these files
    is a snapshot from Red's last published index, it can disagree with Red's own copy
    between runs, and that is cheaper than the fetch it removes. Additions are new keys,
    never changes to existing ones. Where the payload is a list of objects, as
    popularity and stats and ratings are, each object gains the metadata fields. Where a
    document is named as a bare string, as the subject detail's documents array does, a
    sibling map keyed by identifier is added rather than that array becoming a list of
    objects: retyping an existing key is what would break a caller, and it is what Reef
    is asking Red not to do to it, so the precomputer holds itself to the same rule. A
    map also grows a field without retyping anything. The invariant becomes that a
    precomputed file is the live response plus added keys, still testable by stripping
    the additions and comparing. Metadata rfcmeta cannot resolve is null rather than
    omitted or echoed back as the identifier, so a reader can tell "no such document"
    from "not looked up". Which fields a row needs beyond title is the open item below.
    Commit: "Add document metadata to precomputed reads".
23. Stale-source guard: a run warns when a document Reef holds cannot be resolved
    against Red's index, and separately when the index is older than
    REEF_RFC_INDEX_MAX_AGE_DAYS, defaulting to 30. The unresolved-document check is the
    real signal and the age one is a backstop, which is the opposite of how this
    started. Red rebuilds the index when RFCs are published rather than on a clock, and
    RFC publication is bursty: over the last five years the gaps between publication
    dates run to a median of 3 days, p95 of 12 and a maximum of 23, so a threshold tight
    enough to catch a stopped pipeline quickly would fire through every ordinary quiet
    fortnight. Thirty days is above every gap observed in five years and roughly triple
    p95. The deeper reason age is weak is that a frozen index does no harm while no RFCs
    are being published: what harms Reef is a document it knows about that Red's index
    does not have, which happens the moment somebody rates or files a newly published
    RFC, and is exactly what the unresolved check sees. So the age warning exists only
    for the case where Red's pipeline has died and no publication has yet exposed it.
    Neither ever blocks a run: Reef's own payloads are correct whatever Red's age, only
    the titles are old, and coupling Reef's exit status to Red's uptime would make a Red
    outage read as a Reef failure. createdOn and its age are logged on every run
    regardless, since that is the line somebody will want when debugging. Commit: "Warn
    on a stale or incomplete Red index".
24. Scheduling: django-celery-beat, with the default entries in CELERY_BEAT_SCHEDULE so
    that DatabaseScheduler materialises them on first start and staff can retime one in
    the admin afterwards without a deploy. Two entries, because the halves go stale for
    different reasons: precompute_engagement hourly for stats and ratings, which move
    whenever a reader rates or subscribes, and precompute_all daily, which is the only
    thing that notices an RFC Red has published, since nothing in Reef's own tables
    moves when that happens. detect_rfc_changes runs daily at 04:00, after the full
    precompute has refreshed the shared index it reads. A further task,
    precompute_curated, is enqueued by signals rather than scheduled: popularity,
    subjects and surveys are edited deliberately by staff who then expect to see the
    change published. Reader-driven models are deliberately not wired to signals,
    because a task per rating would enqueue thousands to rebuild a file nobody reads in
    between. Signals fire on_commit, so a rolled-back save publishes nothing, and with a
    countdown so that a few edits in one sitting usually collapse into one run; several
    edits further apart still produce several runs, which is accepted rather than solved
    because debouncing properly needs shared state development has no backend for. Runs
    are serialised by a Postgres advisory lock: two at once race on the purge, since a
    key absent from the run doing the purging looks stale rather than in flight.
    Advisory rather than the usual cache.add() lock because development is DummyCache,
    where every acquisition would appear to succeed and the guard would be a no-op
    exactly where a developer first meets it; the lock also releases by itself if the
    worker dies, which a cache lock only imitates with a guessed timeout. The tasks run
    on their own precompute queue so that a long run cannot sit in front of subscription
    mail, and no task raises on a failed run, because a raise earns a retry that
    recomputes the same broken thing and an alert for what the next tick fixes by
    itself. In k8s this adds a reef-beat deployment, one replica and Recreate rather
    than rolling, since two beats fire every job twice. Commit: "Run the precomputer on
    a schedule".
25. Titles in the admin: a DocumentTitleMixin adding a title column beside the bare
    identifier in the subject, popularity and document-set admins, which is where staff
    curate against nine thousand documents they otherwise see only as numbers. The index
    behind it is shared with the precomputer rather than fetched per page: reduced to
    the two fields Reef publishes, stored as compressed JSON in the Django cache, and
    memoised per process for a minute on top of that. Compressed because the reduced
    index pickles to 784 KiB against memcached's 1 MiB item cap, and a store over the
    cap fails without saying so, which would turn every read back into a 6.8 MB fetch;
    compressed it is 209 KiB and decodes in 6 ms. The memo is what makes a hundred-row
    changelist a hundred dictionary lookups rather than a hundred decodes. Display reads
    never fetch, only the precomputer does: a page render must not wait on a download
    and a couple of seconds of validation, and a fetching display path would make every
    test touching an admin page reach the network. So a cold cache shows no title rather
    than reporting every row as an unknown document, which is the distinction that
    matters, since "unknown document" is meant to mean a curation error. Development
    gains a LocMemCache, because base is DummyCache and under it every cache write
    succeeds and every read misses, which is how a caching bug hides until staging.
    Commit: "Show document titles in the admin".
26. Subseries in subscription matching: subscriptions_for_document expands the
    changed document to the subseries containing it, so a change to rfc2119 reaches
    somebody who subscribed to bcp14, whether they named it directly, held it in a set
    or carried it on a subject. The membership is read off the shared index rather than
    asked of Red per document, because an index entry's subseries field already is this:
    nothing to invert and no second lookup table worth building. This read fetches when
    the index is cold, unlike the admin's, because skipping the expansion costs somebody
    an email rather than a title. rfcmeta.fetch_doc and fetch_subseries are deleted in
    the same step, having never acquired a caller: the admin reads the shared index and
    so does this, so the per-document and per-container files Red publishes are unused.
    reef/testing.py arrives with it, because two call sites in a row have accidentally
    made the test suite fetch from Red and a third will. Commit: "Expand subseries when
    matching subscriptions".
27. Change detection: a DocumentSnapshot model and a daily task that diffs Red's index
    against it. The datatracker was the assumed source and turns out not to be one: it
    publishes no change feed, no events endpoint and no webhook registration, only a
    document list filterable by publication date. Red's index carries what all six
    subscription kinds need, Reef already fetches and validates it hourly, and diffing
    two real snapshots four days apart produced three new documents and no spurious
    changes at all. Watched fields are appearance, status, obsoleted_by, updates,
    updated_by and subseries; title, authors, abstract and formats are ignored, because
    a typo correction must not mail everybody tracking the document. The snapshot is one
    row holding zlib-compressed JSON, 57 KiB against 878 KiB uncompressed, keyed by a
    fixed primary key so a second row cannot appear. A blob rather than a table with a
    column per field, for two reasons: the watched set will change and a table would
    need a migration each time, and a queryable table of statuses is document metadata
    that something will eventually read as truth, which is the drift the data model
    forbids. Related documents reduce to bare numbers, since keeping Red's {id, number,
    title} would triple the payload and make an upstream title correction look like an
    obsoletion. With no snapshot the run seeds and emits nothing, rather than treating
    all 9,834 documents as new; seeding on first run rather than in a data migration,
    because a migration runs in CI and image builds where a 6.8 MB fetch has no
    business. Commit: "Detect RFC changes by diffing Red's index".
28. Rendering a change: Reef composes the sentence the digest shows, which the templates
    had assumed would arrive written from the feed. One event per document per run,
    changes joined with "; ", so the template's existing `{{ doc_display }}: {{ change
    }}` renders "RFC 2119: Obsoleted by RFC 9999; status changed to Historic". Wordings
    are "Published as <status>", "Obsoleted by <doc>", "Updated by <doc>", "Status
    changed to <status>", "Added to <subseries>" and "Removed from <subseries>", taking
    the status name from the index's own status.name and document names through
    display_doc_id. The url is https://www.rfc-editor.org/info/rfc9110/, which is Red's
    canonical form: /rfc/rfc9110 302s to it. Commit: "Render RFC changes as digest
    lines".
29. Per-subscriber coalescing: send_subscription_digest takes a user rather than a
    subscription, and subscriptions/mail/_subscription_line.txt becomes a list of the
    reasons somebody is being written to rather than one sentence. This closes the
    notification-volume open item, which no amount of care inside the task could: a task
    sees one subscription, so a reader who holds an rfc subscription and a set
    containing the same document gets two mails about one change unless the caller
    groups them. Events are grouped by user and deduplicated by document before anything
    is enqueued. Commit: "Coalesce notifications per subscriber".
30. Durable notifications: a persisted row per pending mail, with the task carrying only
    its primary key, following Purple's MailMessage. Reef's RabbitMQ has no persistent
    volume, so a broker restart drops every queued message and Celery's own durability
    does not reach; putting the body in Postgres moves the guarantee to where the data
    already lives. The row carries the reader, the subscriptions that matched, the
    events, attempts and a sent stamp: the arguments rather than the rendered message,
    unlike Purple's, because the body derives from data still in the database and
    re-rendering at send time is how a reader who unsubscribes in between stops getting
    the mail. Delivery is at-most-once through the stamp, and a sweeper re-enqueues
    anything left unsent, which is what recovers a broker that lost its queue. The row
    is then deleted, as Purple deletes its MailMessage: this is a queue rather than a
    log, the mail itself already tells its reader why it arrived, and keeping a
    per-reader history of everything Reef has said would be a retention question for a
    service that otherwise keeps almost nothing about people. What stays in the table is
    what is still owed, plus the few that could never be delivered. The snapshot
    advances only after the rows are written, so a crash repeats a run rather than
    skipping one: duplicates are recoverable and a missed change is not. Commit:
    "Persist notifications before enqueuing them".
31. Ingest wiring: the daily task runs detection, resolves each change through
    subscriptions_for_document and the predicate kinds, coalesces per subscriber, writes
    the notification rows and enqueues them. ingest_rfc_change and its event-dict
    interface go: they were shaped for a feed that does not exist, and the diff is the
    caller now. The predicate kinds resolve here rather than by join, as the model
    always said: new_rfc is a document appearing, by_status is a document appearing with
    that status, obsoleted is obsoleted_by gaining an entry or the status becoming
    historic. A run also warns if Red's index createdOn has not moved since the last
    one, because a frozen index produces no diff and therefore no mail, and nothing else
    would notice. Commit: "Notify subscribers of RFC changes".
32. Subject lifecycle: retiring, merging, and refusing a delete that would take
    somebody's subscription with it. subjects/merge.py holds the merge because it is not
    one write -- it moves two kinds of row, decides what to do about a reader who
    follows both, retires the source and tells everybody affected -- and a model method
    doing the first four and leaving the fifth to whoever remembered is how a
    subscription changes meaning silently. SubjectDetail serves a retired subject as
    slug, retired and merged_into alone: the precomputer renders the view, so the
    endpoint has to return the redirect itself or the published file and the live
    response would disagree, which is the invariant every other precomputed file holds
    to. Commit: "Retire and merge subjects".

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
- Precomputer: manage.py precompute with no bucket configured writes the whole set of
  files under ./precomputed and exits 0; a named task writes only its own; --doc narrows
  the per-document files while still rebuilding the whole-series ones; --dry-run writes
  nothing. Stripping the added metadata keys from a precomputed payload leaves the live
  endpoint's response byte for byte, and a task that adds nothing matches it directly. A
  document identifier rfcmeta cannot resolve carries null metadata rather than being
  dropped from the file, and the run warns naming it. A run whose mini index is older
  than REEF_RFC_INDEX_MAX_AGE_DAYS warns and still exits 0, and one that cannot reach
  Red at all still writes every payload, with metadata null throughout. A second run
  after deleting a rating purges that document's file and leaves a key no task owns in
  place; a run in which one task raises still refreshes the others, exits 1, and purges
  nothing. Naming a bucket without credentials is refused rather than falling back to
  the directory. All covered by manage.py test; the S3 path itself is exercised by hand
  against MinIO.
- Scheduling: celery beat starts with DatabaseScheduler and creates the precompute-all
  and precompute-engagement periodic tasks; a worker started with
  --queues=celery,precompute binds both; dispatching precompute_engagement writes
  stats.json and the ratings files from the worker. Saving a curated model enqueues
  precompute_curated after the countdown, a rolled-back save enqueues nothing, and a
  rating enqueues nothing. Two runs landing together leave one running and one logging
  that another holds the lock, both succeeding. All but the beat and queue bindings are
  covered by manage.py test; those were exercised by hand against the dev containers.
- Document metadata: rfcmeta resolves rfc9110 to its title and std97 from Red's live
  index, expands bcp14 to RFC 2119 and RFC 8174, and returns None for an identifier that
  names nothing. Every precomputed file that names a document carries title and
  subseries; the subject detail keeps its documents array and gains a document_meta map
  beside it. Stripping the added keys from a payload leaves the live endpoint's response
  byte for byte, and a task that adds nothing matches it directly. An unresolvable
  identifier carries null metadata rather than being dropped, and the run warns naming
  it. Red unreachable still writes every file, with null metadata throughout. An index
  older than REEF_RFC_INDEX_MAX_AGE_DAYS warns and the run still exits 0. The index is
  fetched once per run, not per lookup. All covered by manage.py test, with Red's index
  stubbed so the suite neither reaches the network nor validates ten thousand entries
  per test.
- Admin titles: with the index warm a row shows its title, canonicalising the
  identifier first; with it cold the column is blank rather than reporting every row as
  unknown; a document the index lacks is called out as unknown, which is a curation
  error and the only place Reef would notice one. Rendering a row never reaches Red, and
  a hundred rows cost well under a millisecond. The shared cache is compressed, a
  corrupt entry is discarded and refetched, a failed fetch is not memoised, and each
  caller gets its own miss tracking.
- Subseries matching: a change to rfc2119 matches an rfc subscription to bcp14, a set
  holding bcp14 and a subject assigned to bcp14; a change to a document in no subseries
  matches only subscriptions naming it; a subscriber reached both directly and through a
  subseries is returned once. With no index reachable the expansion is skipped and
  warned about rather than raising. Verified against Red's live index: rfc2119 and
  rfc8174 report bcp14, rfc9110 reports std97, rfc8446 reports none.
- Change notification: a first run with no snapshot seeds and sends nothing; a second
  run with an unchanged index sends nothing; a document appearing notifies new_rfc, a
  by_status subscription matching its status, and anybody whose rfc, set or subject
  subscription names it or a subseries containing it. Gaining an obsoleted_by entry or
  becoming historic notifies obsoleted. A title-only change notifies nobody. A reader
  holding two subscriptions that both match one change gets one mail naming both
  reasons. Notifications are written to the database before being enqueued, are not sent
  twice across a redelivery, and survive the broker losing its queue. A run whose index
  createdOn has not moved since the last one warns. All covered by manage.py test.
- Tests do not reach the network. reef.test_runner refuses urlopen for the whole
  suite, because three separate call sites reached Red's index without anybody noticing
  until the suite got slower: the admin title column, subscription matching and change
  notification. Anything wanting the index stubs it with reef.testing.stub_rfc_index,
  and anything exercising the fetch patches urlopen itself, which overrides the refusal.
- Unsubscribing: with REEF_REQUIRE_UNSUBSCRIBE_URL set and no URL configured, a digest
  is held rather than sent or discarded, its attempts stay at zero so the sweeper keeps
  offering it, and it goes out in full with both the line and the List-Unsubscribe
  header once the URL is set. Development sends without one, so mail can be exercised
  against mailpit.
- Subject lifecycle: a followed subject cannot be deleted and an unfollowed one can; a
  retired subject leaves the vocabulary, keeps matching for its existing followers, and
  refuses new ones; merging moves documents without duplicating them, repoints
  followers, keeps one subscription for somebody who followed both, and refuses a merge
  into a retired subject or a subject into itself. Everybody affected gets one notice. A
  retired subject resolves at /subjects/<slug>/ to slug, retired and merged_into and
  nothing else, and its precomputed file is that same redirect, while the vocabulary
  file omits it.
- Subseries membership: a document joining a subseries reaches somebody following the
  container, and so does a document leaving one, including through a set or subject that
  holds the container. A change that is not about membership does not reach them.
- Overlapping runs: a change-notification run that cannot take the lock does nothing
  at all -- no notifications written and the snapshot left where it was -- and the next
  tick finds the lock free. The lock is per Postgres session and re-entrant within one,
  so it guards against two workers rather than against one process calling twice, which
  is what two runs of a scheduled job actually are.
- Checks: ruff check and manage.py test; manage.py spectacular --validate; npm run lint
  and typecheck in client/.
- Modes: build images; run with REEF_DEPLOYMENT_MODE=production and real env;
  Creator and Analytics load without a watermark; CSP serves only self-hosted bundles.
- k8s: kubectl kustomize k8s/overlays/staging renders and dry-run applies.

## Open items

- open/ API contract with Red (#225): agree the exact response shape (id, slug, title,
  description, url) and user-targeting semantics. Red owns taken/dismissed tracking, so
  Reef stays stateless there. This is a cross-repo dependency.
- Subscriptions depth (#139): built, as steps 27 to 31. What the ticket called
  datatracker change-feed ingestion is a diff of Red's published index, because the
  datatracker has no feed to ingest. Three things to watch now that it runs. The param
  key for by_status ("status") was named ahead of its matching logic and is now matched
  against Red's status name lowercased, so the two have to stay in step if Red renames a
  status. Unsubscribe is a hard delete, so the uniqueness constraint is unconditional,
  which has to become partial if a soft unsubscribed_at or a pending-verification state
  is ever added, or it will block resubscribing. And notifications now depend on Red's
  precomputer running: a frozen index yields no diff and so no mail, which the daily
  precompute run warns about through the index age check rather than the notification
  run, since an unmoved createdOn is the ordinary quiet case and warning on it daily
  would be noise.
- Subscribing by bare email: not doing it. The data model once said "user or email",
  and Subscription is user-only, which is now the settled answer rather than an
  unfinished one. Every address comes from Authentik, which has verified it, so Reef
  needs no verification flow, no pending state and no second message. It also avoids
  what the column would have cost: a fourth nullable identity column in the uniqueness
  constraint, alongside user, the set FK and the subject FK, where Postgres counts NULLs
  as distinct and only nulls_distinct=False holds it together. If anonymous subscription
  is ever wanted, it is a new design and not a column.
- Subseries as an event: settled. A BCP or STD is a container whose membership
  changes, and "updates to bcp14" meant two things: a change to a constituent RFC, and a
  change to which RFCs constitute it. Both are now matched. A constituent changing
  reaches the container's followers because subscriptions_for_document expands to the
  subseries holding the document. A document joining one reaches them for the same
  reason, since it is a constituent by the time the run looks. A document leaving one
  needed the snapshot: current membership no longer names the container, so nothing in
  the index could find the people following it, and the diff of the subseries field is
  the only record that it happened. The remaining question is not detection but wording
  -- a departure is announced as "RFC 2119: Removed from BCP 14", which reads correctly
  to somebody following either, and there is no separate message written from the
  container's point of view.
- Retiring and merging subjects: built. Three operations that used to be one delete.
  Deleting is now refused while anybody follows the subject, because
  Subscription.subject is PROTECT rather than CASCADE and Django's admin reports the
  refusal; an unfollowed subject created by mistake still deletes. Retiring sets
  retired_at, which takes the subject out of the vocabulary and out of the picker and
  refuses new subscribers, while leaving existing subscriptions matching, so the
  population decays instead of being cut off; clearing retired_at brings it back.
  Merging moves the assignments, repoints the followers, deletes rather than repoints a
  subscription for somebody who already follows the target since the uniqueness
  constraint holds only one, retires the source with merged_into pointing at the target,
  and tells everybody affected -- including the reader whose own subscription did not
  move but changed meaning. The notice goes through the ordinary notification queue as
  an event with no document, so it inherits being written down before it is enqueued,
  one mail per reader, and the unsubscribe hold. What is left open is the wording of
  that notice, which is composed in subjects/merge.py rather than in a template, and
  whether a merge should ever be undoable: unretiring the source brings it back empty,
  since its documents and followers have gone.
- Assigning subjects at scale: the vocabulary is small and staff can type it, but the
  back catalogue is roughly 9,800 RFCs and the admin assigns one document at a time.
  Nothing decides whether subjects apply only to documents published from now on, or
  whether the catalogue gets backfilled, and if so from what. Two things have moved
  since this was written. Staff no longer have to curate against bare identifiers:
  rfcmeta can put a title beside each one in the admin, which is the cheapest
  improvement available here and is worth doing whatever is decided about backfilling.
  A bulk import has no source in the sweep, though: the mini index carries a title but
  not abstract or keywords, which are the fields anyone would try to derive subjects
  from, and they are rfc-common only, so reading them for the back catalogue is 9,800
  fetches. Deriving a curated vocabulary's assignments from keywords would be a guess
  dressed as data in any case, and the point of hosting the vocabulary here was to
  decide it rather than read it. Assigning at scale is still
  the practical unknown in subjects.
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
- Notification volume: settled. Delivery is send_subscription_digest(user, matched
  subscriptions, events), one call is one mail, a batch of changes to a set arrives as
  one digest, and a reader holding both a set and an overlapping rfc subscription now
  gets one mail naming both reasons. What made that possible was moving the grouping out
  of delivery: the task sees one reader, and detect_rfc_changes groups by reader and
  deduplicates by document before writing anything.
- Unsubscribing: notifications carry List-Unsubscribe pointing at
  REEF_SUBSCRIPTIONS_URL, a page on Red, because Reef has no unsubscribe route of its
  own. That setting is empty in every environment and no deployment supplied it, which
  mattered little while nothing sent mail and matters a great deal now that the daily
  run does. Production therefore refuses: REEF_REQUIRE_UNSUBSCRIBE_URL holds digests in
  the database, unsent and with their attempts untouched, until the URL is configured,
  so a first deployment cannot quietly send mail that nobody can stop. Two things are
  still open. The URL itself needs Red to have the page, which is the same cross-repo
  conversation as the open/ contract. And true one-click unsubscribe (RFC 8058) needs an
  unauthenticated tokenized endpoint to POST to, which Reef does not have and which is
  why List-Unsubscribe-Post is deliberately absent: offering the header without the
  endpoint would advertise a capability that does not answer.
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
  for a build-time caller and wrong for a hot public endpoint. The precomputer answers
  the build-time half — Red can read stats.json from the blob store and never touch
  the endpoint — so what is left is the case where something wants the numbers live
  and often. If that arrives, this still wants caching or a materialized counter
  rather than a live aggregate, and the precomputer is not a substitute, because its
  files are only as fresh as its last run. Decide when Red's usage is known.
- Writing the additive-only guarantee down on Red's side. Most of this is now done.
  The mini index has its own Zod schema in Red rather than being a Pick of RfcCommon,
  that schema is exported to JSON Schema and committed, a comment on it says Reef reads
  the published file and that fields are only added, and a test fails if the committed
  schema falls behind the definition. What is left is narrower and in two parts.
  rfc-common and info-subseries have no schema and no comment, so the guarantee covers
  them only by conversation. And the synced copy in reef/schemas/ can fall behind Red's
  without anything noticing, which is the accepted cost of not fetching the schema at
  runtime; if that stops being acceptable, the fix is a scheduled job that diffs the two
  rather than a runtime fetch, so that Reef still validates offline.
- Which document fields a precomputed row carries. The rule is that a file serves a
  route in one fetch, so the field set is whatever Red's rows render, and that is Red's
  to name rather than Reef's to guess. Title is certain, and subseries is now settled:
  the mini index did not carry it, and rfcToRfcMini was changed in Red to pass it
  through, which is what the additive-only guarantee is for. What the mini index carries
  is number, title, published, authors, formats, identifiers, status, stream, obsoletes,
  obsoleted_by, updates, updated_by and subseries. What it does not, and an earlier
  draft of this item wrongly said it did, is abstract and keywords, along with area,
  group and pages: those are rfc-common only, so wanting one of them means either a
  second change in Red or a per-document fetch, which is a different cost from a field
  that is already in the sweep. Worth settling in the same exchange as the open/
  contract and the additive-only guarantee, since all three are the same cross-repo
  agreement about shapes. Until then title and subseries are the safe subset, and adding
  a field later is additive.
- Nuxt OIDC client registration: confirm a public (PKCE) Authentik client for the runner
  versus reusing Red's client configuration.
- Survey targeting: the audience and user-specific-offer rules (subscription-driven)
  need a concrete specification beyond the scaffold.
```
