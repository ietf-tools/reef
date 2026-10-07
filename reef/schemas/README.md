# Schemas Reef validates other people's data against

Each file here is a subset of an upstream schema: the part of someone else's data
that Reef reads, and no more.

## rfc-index.schema.json

A subset of Red's upstream schema for
`https://www.rfc-editor.org/api/v1/rfc-index.json` (`RfcCommonSchema` and
`RfcIndexSchema`, in `website/app/utilities/rfc-validators.ts`), covering what
`reef.rfcmeta` reads. It changes when Reef starts or stops reading a field.

### What it requires

- `createdOn` and `index` at the top: change detection refuses an index older than
  the last one it compared, which needs the date.
- `number`, `title` and `status` (`slug` and `name`) on every entry: the
  identifier, what every page shows, and what change detection compares.
- `acronym` and `name` on `area` and `group`, and `type` and `value` on each of
  `identifiers`, when those are present at all: Reef publishes them as they are and
  its own contract requires both.

Every other field Reef reads is optional and only type-checked, because Reef
degrades to an empty value without it: `stream`, `authors`, `published`,
`keywords`, `pages`, `abstract`, `subseries`, and the `number` of each entry in
the four relation fields.

### What it deliberately does not do

- **Forbid unknown keys.** There is no `additionalProperties: false` anywhere. Red
  has undertaken to change this file additively, and Reef holds up its end by not
  rejecting keys it has not seen; forbidding them would turn every field Red adds
  into a Reef outage.
- **Enumerate values.** Statuses, streams, subseries types and the like are plain
  strings. A status Red adds is news to report, not a malformed index, and a closed
  list here would refuse the whole series over it.
- **Describe fields Reef does not read**, such as `formats` or an author's
  `email`: checking them would make Reef fail on changes it would never have
  noticed.

What that leaves:

- a required field being **removed** fails
- a field Reef reads being **retyped** fails
- a field being **added**, at any depth, passes, as does any change to a field Reef
  does not read
