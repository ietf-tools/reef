# Schemas for the data Reef publishes

Files here describe the payloads Reef's precomputer writes to the blob store.
They are the source, not exports: plain JSON Schema, hand-written, and the thing
both Reef and Red validate against.

This directory is the mirror of `reef/schemas/`, which holds the other
direction: schemas for data Reef *reads*, owned elsewhere and copied in.

## subjects.schema.json

Describes `subjects.json`, the subject vocabulary as a tree with every
assignment and every document title. It does **not** describe
`/api/reef/subjects/`, which returns a list and carries no document metadata;
that endpoint is in `reef_api.yaml` like every other.

### Why it is not generated

The artifact has to be language agnostic, because Red validates against a copy
of it. Generating it from a Pydantic model or from the DRF serializers would
make the shared file a by-product of one consumer's language, add a dependency,
and put a staleness test between the model and the file in exchange for nothing
Red can use.

What a hand-written schema normally costs is drift, the file describing what
somebody believed the builder emitted. That is answered by where it is enforced:
`precompute` validates the rendered payload on every run, before uploading, so a
builder that drifts fails the next run rather than whenever a test is
remembered. A failure fails the run and publishes nothing, leaving the previous
payload in the bucket.

### Syncing into Red

Reef owns this file. Red holds a copy and derives its Zod from that copy; Red
does not generate a schema of its own. Both repositories are siblings under
`~/Code`:

```
cp ~/Code/reef/precomputer/schemas/subjects.schema.json \
   ~/Code/red/precomputer/schemas/subjects.schema.json
cd ~/Code/red && npm run generate:zod
```

Nothing detects that Red's copy has fallen behind this one. That is the same
accepted cost `reef/schemas/README.md` records for the traffic going the other
way, and the same trade: a shape change arrives as a reviewable diff in a pull
request rather than as a runtime surprise.

### Permissive here, strict in Reef

This file carries no `additionalProperties: false`, and Red validates with it
exactly as committed. `reef/schemas/README.md` argues the point from the other
side — rejecting unknown keys "would turn every field Red adds into a Reef
outage" — and Reef is the producer here, so it owes Red the same additive
guarantee: new keys may appear, existing ones do not change meaning or go away.

Reef's own validation is strict, and the file does not change for it to be.
`precomputer.schemas.strict()` tightens the loaded copy in memory before
building the validator, because a key Reef did not mean to write is a bug in
Reef rather than somebody else's additive change. There is deliberately no
strict variant on disk: that would be the copy that eventually got sent.

What that leaves, for both sides:

- a declared field being **removed** fails, because everything is in `required`
- a field being **retyped** fails
- a field being **added** passes for Red, and fails for Reef
