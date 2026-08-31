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
popularity.json                     the curated most-popular list
subjects.json                       the whole vocabulary
subjects/<slug>.json                one subject and the documents carrying it
surveys/open.json                   surveys a visitor may be offered
surveys/<slug>/definition.json      an open survey's definition and theme
ratings/<doc>.json                  a rated document's public average and count
```

## Purging

A full run deletes keys that a task owns but no longer produces, so a renamed
subject does not leave `subjects/<old-slug>.json` behind — a stale payload in a
blob store outlives the row it came from indefinitely. Only keys matching a
task that just ran are considered, so anything else sharing the bucket is left
alone. The purge is skipped after a failed task, and under `--doc`, because in
both cases a missing key may just be one this run did not rebuild.

## What is not precomputed

Everything here is rendered as an anonymous caller, because a key in a blob
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
