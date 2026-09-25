# precomputer

Precomputes Reef's public API responses and writes them to a blob store,
because:

- `/api/reef/stats/` is unpaginated by design (Red wants the whole series in
  one call) and aggregates every rating, subscription and set entry on each
  request, which gets slower as engagement grows
- serving a file rather than a query makes the numbers Red builds against
  resilient to Reef being slow or down

This is Reef's counterpart to Red's `precomputer/`. Red's has five entry
points — `all`, `single`, `multiple`, `cron` and `publish` — that differ only
in what they select and how they report. Both are arguments, so this is one:

```
manage.py precompute
```

Red's precomputer imports the website's Zod schemas so the precomputed file and
the live response cannot describe different shapes. Here the two are one
codebase, so `render.py` runs the view itself: what lands in the store is the
byte string the API would have returned, through the same serializer, renderer
and permission checks. There is no second definition to drift.

That holds for every key here, including the two subject files, whose views are
in `subjects/precompute.py`. Those are deliberately not routed -- a published
file needs no URL, and an unpaginated read of the whole vocabulary would be a
cost with no caller -- so they reach `reef_api.yaml` through `reef.urls_contract`
instead, which is `reef.urls` plus them and which nothing serves. The contract is
the only description of these payloads there is; Red derives its Zod from it.

## Usage

```
manage.py precompute                       # every task; the cron job
manage.py precompute stats subjects        # named tasks
manage.py precompute --doc rfc9110         # one document's per-document files
manage.py precompute --dry-run             # render and report, write nothing
manage.py precompute --callback-url URL    # POST {type, message} when finished
manage.py precompute --no-purge            # keep keys the run no longer produces
```

Exit status is 0 only if every selected task produced every file it meant to.

## Where it writes

Naming `REEF_PRECOMPUTE_S3_BUCKET` selects S3 (or any S3-compatible service,
via `REEF_PRECOMPUTE_S3_ENDPOINT`). Leaving it empty writes to
`REEF_PRECOMPUTE_DIR`, defaulting to `./precomputed` — the development
fallback, so a run needs no object storage. Which backend is in use is decided
by configuration alone, never by a command-line argument, so a deployment
cannot be talked into writing production payloads into its own container.

Naming a bucket without credentials is an error rather than a silent fallback.

## Layout

```
stats.json                          every document with any engagement
popularity.json                     the popularity ranking, most popular first
subjects.json                       the vocabulary as a tree, with every
                                    assignment and every title, in one file
subjects/<slug>.json                one subject and the documents carrying it
surveys/open.json                   surveys an anonymous visitor may be offered
surveys/published.json              every published survey, with its visibility
surveys/<slug>/definition.json      an open survey's definition and theme
ratings/<doc>.json                  a rated document's public average and count
```

### subjects.json

Red fetches it per route and renders the page from it, so it carries the titles,
which the served list endpoint has none of to give: Reef holds no document
metadata and resolves it from Red's own index when the view runs.

Two maps, both keyed, so that a caller looks a subject or a document up directly
rather than building an index of its own or scanning an array:

```
documents   { "rfc9110": { title, subseries }, ... }   every document, once
subjects    { "dkim": { id, name, description,
                        parent, path, children,
                        documents,                     direct assignments, ids only
                        document_count,
                        document_count_deep }, ... }
```

Two properties are load-bearing and cheap to break. Metadata is carried once and
referenced by identifier, rather than sitting beside each subject that covers the
document, which at this vocabulary's depth would repeat every title about three
times over. And a subject's subtree is not written out: it is derivable from `path`
and `children` in the pass a caller is already making, and writing it would store
every identifier once per ancestor.

### The two survey lists

`surveys/open.json` is the served `/api/reef/surveys/open/` without a
credential, byte for byte. `surveys/published.json` is every published survey
whatever its visibility, so Red can offer an authenticated-only survey to a
signed-in reader without asking the API who they are first; it picks by each
row's `visibility` and offers an `authenticated` one only to a reader it has
signed in. Its view is `surveys/precompute.py`, routed nowhere, like the subject
ones.

Two things that file is not. It is not a cache of any response: an identified
caller of `/api/reef/surveys/open/` also has the surveys they have already
answered dropped, which one payload serving every reader cannot do, so a row
means "published, and offerable to a reader of this visibility" and no more. And
it is not anonymous-safe in the sense the rest of this store is -- see below.

### subjects/&lt;slug&gt;.json

The served `/api/reef/subjects/{slug}/` response plus two sibling maps:
`document_meta`, the title of each document assigned to the subject, and
`subject_meta`, the curated names of its ancestors and children. One file answers
a subject page in Red in a single fetch, breadcrumb included, without reading the
index for one word.

Maps rather than changes to the arrays they describe. Retyping `documents` into a
list of objects, or `children` into one, is the change that breaks a caller, and
it is what Reef asks Red not to do to it.

A retired subject and an alias are published here too, as the redirect stubs the
served read returns, because a blob store cannot answer with a 301. Neither
carries `documents`, so neither gains the maps.

## Files are the API responses

Every file is byte for byte what its endpoint serves to an anonymous caller, so
the schema in `reef_api.yaml` describes the file as well as the endpoint, and a
consumer generating types from the contract can read the file with them. The
tests hold each task to that: the file's bytes equal the live response.

The subject files are the ones that carry document titles and metadata, and they
do it inside the contract: their views declare it on serializers of their own
(`subjects/precompute.py`) and are described under `/api/reef/precomputed/` in
the schema. `stats`, `ratings` and `popularity` name documents by identifier
only; their readers already hold the documents.

Red's index is still loaded once per run. The subject views resolve titles from
it, and every document `stats` and `ratings` name is looked up in it so that the
run can warn, at its end, about any that Red's index lacks. That miss is the
real staleness signal: a frozen index does no harm until Reef holds a document
Red's copy does not. Red being unreachable costs that check and nothing else;
every file is still written.

The index is fetched and schema-validated at most once an hour and shared with
anything else in Reef that resolves an identifier, rather than fetched per run or
per lookup. Pass `--no-metadata` to skip the fetch and the check, for working
offline.

## Pushing changes to Red

Red bakes each document's rating average and subscriber and set counts into its
own precomputed page data, read from `stats.json` at the time Red's precomputer
runs. So a rating reaches readers only after two runs, in order: Reef republishes
`stats.json`, then Red rebuilds that document's page.

Reader writes do not run the precomputer directly. A rating, a set entry or an
`rfc` subscription marks its document in `PendingDocumentChange`, one row per
document however many writes arrive. Every five minutes
`push_document_changes` takes the marked documents that have not been written
to for `REEF_DOCUMENT_CHANGE_QUIET_SECONDS`, runs `stats` and `ratings --doc` for
them, and POSTs their RFC numbers to `REEF_TRIGGER_RED_PRECOMPUTE_URL`, the
EventListener of Red's `precompute-multiple` Tekton pipeline, in batches of
`REEF_RED_PRECOMPUTE_BATCH_SIZE`. Rows are deleted only once Red has been told;
a failure leaves them for the next tick, and the hourly and daily runs remain
the floor. With the URL unset Reef publishes and Red picks the change up on its
own daily run.

## Purging

A full run deletes keys that a task owns but no longer produces, so a renamed
subject does not leave `subjects/<old-slug>.json` behind — a stale payload in a
blob store outlives the row it came from indefinitely. Only keys matching a
task that just ran are considered, so anything else sharing the bucket is left
alone. The purge is skipped after a failed task, and under `--doc`, because in
both cases a missing key may just be one this run did not rebuild.

## What is not precomputed

What a key holds is what an anonymous caller may read, because a key in a blob
store is served to whoever asks for it. Four endpoints are excluded on purpose:

- `me/documents/` and `subscriptions/` are per-caller by definition.
- `surveys/` and `surveys/<pk>/results/` are staff-only.
- `sets/<uuid>/` reads without a credential, but only because holding the
  unguessable id *is* the permission. That does not survive a store whose keys
  can be listed, and a set is edited by its owner between runs, so a
  precomputed copy would be both a leak and stale on the page showing it.

`ratings/<doc>/` is included as its anonymous body: the public average and
count, with `your_rating` null. That is the field the live response varies by
caller in, and what an unauthenticated reader would have been served.

`surveys/published.json` is the one deliberate exception. Listing the surveys
only a signed-in reader may be offered is the point of the file, so their titles
and descriptions are readable by anyone who fetches the key. What stays behind
the credential is the rest: a definition -- the questions -- is precomputed for
open surveys alone, and responses and results are not precomputed at all. Staff
who would rather a survey's existence not be public should leave it in draft
until it is offered, or accept that publishing it publishes its title.
