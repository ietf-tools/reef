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
popularity.json                     the curated most-popular list
subjects.json                       the vocabulary as a tree, with every
                                    assignment and every title, in one file
subjects/<slug>.json                one subject and the documents carrying it
surveys/open.json                   surveys a visitor may be offered
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

## Document metadata

Every file that names a document also carries that document's `title` and
`subseries`, resolved through `reef.rfcmeta` from Red's published index. The
reason is Red's rather than Reef's: an SPA route wants one resource, and a page
that fetches a list of identifiers and then has to resolve them loads slower
than one that fetches a file it can render.

Additions are new keys, never changes to existing ones. Where the payload is a
list of objects — `stats`, `popularity`, `ratings` — each object gains the
fields. Where a document is named as a bare string, as the subject detail's
`documents` array does, a sibling `document_meta` map keyed by identifier is
added rather than that array becoming a list of objects. Retyping an existing
key is what breaks a caller, and it is what Reef asks Red not to do to it.

So the invariant is that a precomputed file is the live endpoint's response plus
zero or more added keys, which stays testable: strip the additions and compare.

A document Red's index does not have gets `{"title": null, "subseries": []}` —
null rather than omitted or echoed back as the identifier, so a reader can tell
"no such document" from "not looked up". The run warns and names it, which is
the real staleness signal: a frozen index does no harm until Reef holds a
document Red's copy lacks. Red being unreachable costs titles and nothing else;
every file is still written.

The index is fetched and schema-validated at most once an hour and shared with
anything else in Reef that resolves an identifier, rather than fetched per run or
per lookup. Pass
`--no-metadata` to skip the fetch and write nulls, for working offline.

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
