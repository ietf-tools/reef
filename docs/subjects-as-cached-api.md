# The precomputed files as an API cache

Delete `precomputer/schemas/subjects.schema.json` and the schema machinery around
it, and let `reef_api.yaml` be the only description of what Reef publishes. Red
derives a Zod schema from the contract it is already sent by hand, and validates
the R2 files with it.

Supersedes `subject-hierarchy.md`, "The schema Red validates against", and the
step 5 commit that implemented it. To be folded into plan.md once settled.

## The problem

Every file in the store is meant to be the bytes a DRF view returned, which is
`render.py`'s whole correctness argument. Two things spoil it: `subjects.json` is
built by hand in `registry._subject_index()` rather than by a view, and every
other file is a view's bytes plus metadata keys `_augment` adds afterwards.
Neither shape is in `reef_api.yaml`, and the hand-written JSON Schema exists to
police the first.

## The fix

Give each precomputed payload a serializer and a view, so the file is exactly
what a view returned and the contract describes it. The schema file then has no
job.

The views are not routed. `render_anonymous` builds a `RequestFactory` request
and invokes the view callable, so its `path` is cosmetic and no URL exists. Only
drf-spectacular needs a urlconf, so `reef/urls.py` stays as it is and
`reef/urls_contract.py` includes it plus the precompute-only views:

    REEF_DEPLOYMENT_MODE=build ./manage.py spectacular \
        --urlconf reef.urls_contract --file reef_api.yaml --validate

`reef/tests_api_contract.py` passes the same flag, so the committed contract
still cannot fall behind the code.

Paths in that urlconf are API-shaped, so it is obvious which read each file is a
cache of, with a description naming the store key it is published to. Nothing is
routed, so `/api/reef/subjects/index/` cannot collide with a subject slugged
`index` and the natural path is free.

`reef_api.yaml` itself does not change: same command, same 3.0.3 output, same
hand-sync into Red.

## Where the files are

One place: the blob store. `get_blob_store()` gives an `S3BlobStore` when
`REEF_PRECOMPUTE_S3_BUCKET` is set — R2, via `REEF_PRECOMPUTE_S3_ENDPOINT` — and
a `LocalBlobStore(REEF_PRECOMPUTE_DIR)` otherwise; production sets
`REEF_PRECOMPUTE_REQUIRE_S3` so it refuses to fall back. Written by
`manage.py precompute` on a celery beat schedule, plus the signals' debounced
rebuild. Nothing in Reef serves them, and `precomputed/` is gitignored, so they
are not committed either.

Nothing copies them anywhere else today. Red fetches them from R2 in production
and, under this plan, uses a copy of a run's output as its local fixtures.

## What this does not change

Who may call Reef live:

- **A browser may**, with the reader's own token. `reef.ts` stays as it is.
- **Red's precomputer may**, at build time, or it may read Reef's R2 assets
  instead. Its choice.
- **A Nuxt server render never may.** Server-rendered routes read R2 and nothing
  else, with no fallback on any path.

And which reads are published: the selected set `precomputer/registry.py` already
documents, not a mirror of the API.

## The files

**`subjects.json`.** A `Serializer` with two `DictField`s — `documents` keyed by
identifier, `subjects` keyed by slug — which spectacular renders as
`additionalProperties: {$ref}`. The entry serializer is `SubjectSerializer` plus
`children` and `documents`. Insertion order carries tree order. Replaces
`_subject_index()`.

**`subjects/{slug}.json`.** One file per subject, which Red's
one-JSON-file-per-route pattern requires: a subject page is a single fetch, and a
retired subject or alias resolves in that same fetch because the precomputer
writes files for those too. Folding them into the index is therefore not an
option.

`SubjectDetailSerializer` plus two sibling maps:

- `document_meta`, keyed by identifier — the RFC titles, as today.
- `subject_meta`, keyed by slug, `{name: ...}` — new, covering the ancestors
  from `path` and the `children`, so the breadcrumb and the child list render
  curated names instead of slugs. `children` is not retyped into a list of
  objects: `[subject].vue` reads it as slugs, and a sibling map is the rule
  `document_meta` already follows. Order stays on `path`. Cheap: the child rows
  are already loaded, the ancestors are one `path__in` on exact paths.

**`stats.json`, `popularity.json`, `ratings/{doc}.json`.** Out of scope. They
could take the same treatment — a serializer carrying `title` and `subseries` —
but their consumer is Red's precomputer appending stats to each info route's
JSON, which is not built. `_augment` survives for them until it is.

**`surveys/open.json`, `surveys/{slug}/definition.json`.** Already a routed
endpoint's bytes with nothing added; they need a contract path or nothing.

Document metadata comes from `rfcmeta.cached_mapping()`, which never fetches and
which the precompute run already warms before any task executes.

## What gets deleted

- `precomputer/schemas.py`: `load`, `strict`, `validate`.
- `precomputer/schemas/subjects.schema.json` and its README.
- `precomputer/tests_schemas.py`.
- `registry._subject_index()` and the subjects `_augment` closure. `_augment`
  itself stays, for the three files left out of scope.
- In Red: the committed schema copy and the `json-schema-to-zod` step.

Nothing generated replaces them in Reef. The guards are the serializer as sole
definition of each shape, `render_anonymous`, `tests_api_contract.py`, and the
byte-equality invariant, which for the subject files now has no augmentation to
strip. `SubjectIndexTests` keeps its rendered-size assertion.

Costs nothing worth listing. Map keys are strings in the contract; an unrouted
view carries none of an endpoint's costs; and nothing in Red reads
`subjects.json` yet, so no file in the store changes.

## Red

One generated Zod file and a util function per file. No Nitro configuration, no
server routes, no caching layer.

**The schema.** A second script beside `generate:reef-api-client`, same input
file, emitting Zod source into `generated/`, regenerated after a sync.
`typed-openapi --runtime zod`, tried against the real contract: plain `zod` with
no client dependency, `nullable` and `pattern` carried through, and
`.catchall(z.unknown())` so unknown keys are kept. It writes `// @ts-nocheck` at
the top, so the generated file is not type-checked. `openapi-zod-client` also
works but pulls in `@zodios/core`; `@hey-api/openapi-ts` fails on this contract.

Both the website and the precomputer want the output, and both already pin `zod`
4.4.3 and `yaml` 2.9.0, but `reef_api.yaml` sits in `website/` alone, so where
the contract and the generated file live is part of the setup.

One thing to watch. `typed-openapi` renders `SubjectDetailOrRedirect`'s
discriminator-less `oneOf` as a union with a refine asserting exactly one member
matches, and Reef's three shapes are disjoint only because `RetiredSubject`
requires `merged_into` and `SubjectAlias` requires `alias_of`. The fixture test
below covers this: parsing a live subject, a retired one and an alias through the
union is what stops the disjointness being an accident nothing checks.

Never `.strict()`. Spectacular emits no `additionalProperties: false`, so the
derived schema keeps Reef's additions additive by construction.

**The fetch.** A util per file: hardcoded URL, `fetch`, `parse`. R2 only, and
what the server-rendered subject routes use. Locally the same functions return
fixtures.

The fixtures are a copy of a precompute run's output, replacing what is there
now. Reef's data clobbers Red's: the three synthetic subjects the current
fixture invents — `aerospace` empty, `packet-switching` retired, `crypto` an
alias — are not preserved, and the tests that need those states build the
payloads themselves, forked from the fixtures rather than depending on them.
That keeps the sync a plain copy with no transformation step to maintain.

Every fixture is parsed through the derived schema in a test. The fixtures are
typed against the generated client, so `test:types` already catches shape drift;
what this adds is the runtime half the type system cannot see — the `pattern` on
a slug or a document identifier, null against absent, and the union's
exclusivity. It costs a dev-time test and nothing a reader pays for.

One consequence to size for. The current fixture is 227KB, capped at 20
documents per subject and carrying no titles. Real output is the full
vocabulary, every one of the sheet's 19,044 assignments, and a `documents` map
of titles — a few megabytes — plus one file per subject, which is 502 of them.
Whether Red commits all of that or only the index and a sample is worth deciding
when it is generated rather than guessed at here.

## Steps

1. `reef/urls_contract.py`, the `--urlconf` flag in the command and in
   `tests_api_contract.py`, and one precompute path to prove the mechanism.
   "chore: describe the precomputed files in the contract"
2. The subject index serializer and view, asserted byte-equal to today's
   `_subject_index()` output, which is then deleted. "feat: render the subject
   index from a serializer"
3. `document_meta` and `subject_meta` on the subject detail precompute view;
   the subjects `_augment` closure deleted. "feat: contract the precomputed
   subject metadata"
4. Delete `precomputer/schemas.py`, `precomputer/schemas/` and
   `tests_schemas.py`; update `precomputer/README.md`.
   "refactor: drop the hand-written precompute schema"
5. Fold the settled decisions from this document into plan.md and delete it, in
   the same commit. Do the same for `subject-hierarchy.md`, whose "The schema Red
   validates against" section this supersedes and which is otherwise a committed
   file giving the wrong answer. "docs: fold the executed subject plans into
   plan.md"
6. Red: the generated Zod file, the fixture sync, the fixture parse test, and
   the two subject utils.

Steps 1 to 5 are invisible outside Reef.

This document is a working file, not a record. It exists to be reviewed and
agreed with Red before Reef commits to a contract, and step 5 is its disposal:
the rationale that outlives it is already in the docstrings of `registry.py`,
`render.py` and the serializers, the decisions belong in plan.md, and the rest
is in git. A design document kept past its execution is a second description of
the code that nothing keeps true — which is the argument this plan makes about
`subjects.schema.json`, and it applies here too.
