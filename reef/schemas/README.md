# Schemas Reef validates other people's data against

Files here describe data Reef reads from somewhere else. They are copies, not
sources: each one is generated in the repository that owns the data, and synced
here by hand.

## rfc-index.schema.json

Describes `https://www.rfc-editor.org/api/v1/rfc-index.json`, the whole RFC
series in one file, which `reef.rfcmeta` reads to resolve an identifier to a
title and the rest of what `subjects/precompute.py` publishes per document.
Red owns the shape; the JSON Schema mirrors Red's own Zod definition
(`RfcCommonSchema` and `RfcIndexSchema`, in `website/app/utilities/
rfc-validators.ts`), because Reef is Python and cannot share it directly.

Reef previously read a smaller "mini" index instead
(`rfc-mini-index.schema.json`, now removed); the full index is a strict
superset of it, so there is no longer a reason to fetch both.

### Syncing

Unlike the mini index this replaced, Red has no `npm run generate:schema`
output for the full index yet -- only `RfcMiniIndexSchema` is wired into
`precomputer/src/utilities/export-json-schema.ts`. This file was hand-derived
from `RfcCommonSchema` directly instead. If Red's own generator is ever
extended to also emit one for `RfcIndexSchema`, the same `io: 'input'` export
(see below) should replace this file rather than sit beside it, the same way
the mini index's copy was kept in sync:

```
cd ~/Code/red/precomputer && npm run generate:schema
cp ~/Code/red/precomputer/generated/rfc-index.schema.json \
   ~/Code/reef/reef/schemas/rfc-index.schema.json
```

Nothing detects that this copy has fallen behind Red's — that is the accepted
cost of not fetching the schema at runtime, and the trade is that Reef
validates offline and a shape change arrives as a reviewable diff in a Reef
pull request.

### What it does and does not catch

The schema carries no `additionalProperties: false`. That is deliberate and
load-bearing: Red has undertaken to change these files additively, and Reef
has to hold up its end by not rejecting keys it has not seen before.
Validating with `additionalProperties: false` would turn every field Red adds
into a Reef outage.

What that leaves:

- a field Reef reads being **removed** fails, if it is in `required`
- a field being **retyped** fails
- a field being **added**, at any depth, passes

The required set is `number`, `title`, `status`, `stream`, `authors` and
`formats`. Everything else Reef reads is optional -- `published`, `area`,
`group`, `keywords`, `pages`, `abstract`, `subseries`, and the four relation
fields -- because the datatracker may omit them and Red passes the absence
through. Their removal is therefore invisible to this check. That gap is
known and accepted; it is optional by intent rather than by oversight.
