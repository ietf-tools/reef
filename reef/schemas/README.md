# Schemas Reef validates other people's data against

Files here describe data Reef reads from somewhere else. They are copies, not
sources: each one is generated in the repository that owns the data, and synced
here by hand.

## rfc-mini-index.schema.json

Describes `https://www.rfc-editor.org/api/v1/rfc-mini-index.json`, the whole RFC
series in one file, which `reef.rfcmeta` reads to resolve an identifier to a
title. Red owns the shape; the JSON Schema is generated from Red's Zod
definition, because Reef is Python and cannot share it directly.

### Syncing

Generated in Red and copied across. Both repositories are siblings under
`~/Code`:

```
cd ~/Code/red/precomputer && npm run generate:schema
cp ~/Code/red/precomputer/generated/rfc-mini-index.schema.json \
   ~/Code/reef/reef/schemas/rfc-mini-index.schema.json
```

Nothing detects that this copy has fallen behind Red's — that is the accepted
cost of not fetching the schema at runtime, and the trade is that Reef
validates offline and a shape change arrives as a reviewable diff in a Reef
pull request. Red's own test suite does catch the other half, where Red's
committed schema falls behind the Zod definition it came from.

### What it does and does not catch

The schema is exported with Zod's `io: 'input'`, so it carries no
`additionalProperties: false`. That is deliberate and load-bearing: Red has
undertaken to change these files additively, and Reef has to hold up its end by
not rejecting keys it has not seen before. Validating with
`additionalProperties: false` would turn every field Red adds into a Reef
outage.

What that leaves:

- a field Reef reads being **removed** fails, if it is in `required`
- a field being **retyped** fails
- a field being **added**, at any depth, passes

The required set is `number`, `title`, `status`, `stream`, `authors` and
`formats`. Everything else is optional, including `published` and `subseries`,
because the datatracker may omit them and Red passes the absence through. Their
removal is therefore invisible to this check. That gap is known and accepted;
`subseries` in particular is optional by intent rather than by oversight.
