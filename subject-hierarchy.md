# Hierarchical subjects

A design for making `subjects.Subject` a tree, written against the draft
assignment sheet (`rfc-tag-assignments.csv`), whose column E already carries the
hierarchy the model does not have: `applications-and-data-formats / ftp`,
`messaging / email / email-authentication / dkim`. To be folded into plan.md's
data model and implementation steps once the decisions below are settled.

## What the draft says

Four properties of the draft settle most of the design, and one of them rules out
the first idea anybody has.

Fourteen roots, four levels, no deeper: applications-and-data-formats,
governance-and-process, humor, internet-layer, iot-and-constrained-networks,
link-layer, messaging, naming-and-discovery, network-operations,
real-time-communications, routing, security, transport, web. The depth ceiling of
four is agreed rather than observed, which matters more than it sounds: it means
no read ever needs recursive SQL, and it bounds the length of any derived path
column.

Column D holds leaf slugs only, and they appear to be globally unique — `dkim`
resolves to exactly one path, which is the only reason the flat tag column can be
expanded to a full path at all. `Subject.slug` is already globally unique, so the
hierarchy costs nothing in identity: the detail URL stays `/subjects/<slug>/`, the
alias table is untouched, and the precomputer's keys stay flat at
`subjects/<slug>.json`.

Interior nodes carry RFCs *and* have children. `security / cryptography` holds
RFC 1751 directly and parents `elliptic-curve`, `md5` and `sha`. So a category and
a subject are not two kinds of thing, and a separate `SubjectCategory` model for
the branches is wrong before it is written. Whatever holds the tree has to be the
same model that already holds the assignments.

And some interior nodes carry nothing at all. `messaging`, `routing`, `web` and
`humor` have no direct assignments in the draft. That is the question that decides
the rest of the work, and it is answered below.

One scale change is worth naming, because plan.md leans on the opposite. The
vocabulary is described there as "small enough to hand to a caller whole" and as
something staff can type. The draft is several hundred nodes. The unpaginated list
read still holds comfortably — a few hundred rows of four short fields — but two
admin controls do not: a `parent` select rendering an option per subject, and
`SubjectAssignmentAdmin.list_filter = ["subject"]`, which renders a filter link
per subject.

## Three decisions

Everything downstream follows from the first of these, so it is worth arguing
before any code is written.

**Does a parent subject cover its descendants' documents?** Recommended: yes.
Assigning `smtp` makes that document count under `email` and under `messaging`
too. The argument is in the next section; it is the decision that turns an empty
branch node from a trap into a non-event.

**Does the existing `documents` key change meaning?** Recommended: no. It stays
the direct assignments, because Red consumes it and because the precomputer keys
`document_meta` off it, and the subtree gets a second key. Silently widening a
published array is a contract change dressed up as a bug fix.

**Do slugs and names stay globally unique?** Recommended: yes, and said out loud
rather than left as an accident of the current schema. The draft supports it, and
it is what keeps the URL, the alias table and the flat key layout as they are. The
cost is real: `security / privacy` and a future `web / privacy` cannot coexist.
Relaxing it later means `unique(parent, slug)` and path-shaped URLs, which is a
breaking change, so this is a decision and not a default.

## Representation

Four candidates, judged against what this vocabulary actually is: a few hundred
rows, four levels, edited by staff a handful of times a month, and read through
precomputed static files.

An **adjacency list** — `parent` as a self-referential FK, beside the `merged_into`
self-FK already there — is the base. One nullable column, truth held locally so
nothing can drift, and no new dependency. What it does not give is tree ordering
or a one-query subtree.

A **derived `path`** column alongside it is what makes the adjacency list useful,
and it is recommended. It earns its keep three times: a tree-ordered listing from
`order_by("path")`, a subtree from one indexed `path__startswith`, and the
ancestors of a node read straight off the string with no query at all. It is also
the same string the draft's column E carries, so import and export round-trip. The
cost is a denormalisation to keep honest — recomputed on rename and on reparent,
with a `rebuild_paths` command and a test asserting that every stored path equals
the path derived from `parent` and `slug`.

**treebeard or MPTT** would buy proven subtree queries and a drag-and-drop admin,
and are not worth it here. Both want to own creation — `add_root` and `add_child`
rather than `objects.create()` — which collides with `LiveSubjectManager`,
`all_objects` and the `save()` override that mints an alias when a slug changes.
Both also need an explicit rebuild after queryset-level writes, and `merge.py`
already does several (`bulk_create`, `aliases.update()`, `assignments.delete()`).
A dependency that has to be remembered in exactly the places this codebase already
writes in bulk is a dependency that will be forgotten once.

A **closure table** would answer "every descendant" in one indexed join, and would
tolerate a DAG if a subject ever needed two parents. At depth four,
`path__startswith` answers the same question, so it is a second table maintained
for a benefit already in hand. Not now, and the reason it might come back is a
second parent rather than performance.

The schema, then:

    # subjects/models.py, added to Subject
    parent = models.ForeignKey(
        "self", null=True, blank=True,
        on_delete=models.PROTECT,      # refuse deleting a node with children,
        related_name="children",       # as Subscription.subject already does
    )
    # Derived from parent and slug. Never edited by hand.
    path = models.CharField(max_length=255, unique=True, db_index=True)
    depth = models.PositiveSmallIntegerField(default=0)

    MAX_DEPTH = 4

`path` is the slugs joined by `/` with a trailing separator, so that a prefix match
cannot straddle a sibling: `security/cryptography/` never matches
`security/cryptography-x/`. Four levels of a 50-character slug plus separators fits
255 with room left. `depth` is derivable by counting separators and is stored
anyway, because it is what the admin indents by and what selects the roots.

Four invariants the model owes:

- No cycles. A node cannot be its own ancestor, checked by walking `parent` at most
  `MAX_DEPTH` times. In `clean()` for the admin form, and again in `save()`, because
  reparenting also happens in code.
- No depth overflow. Reparenting a subtree is what breaks the ceiling, so the check
  is on the deepest descendant and not on the node being moved.
- No live child under a retired parent. A subject that is still offered but
  unreachable in a tree picker is worse than one that is plainly gone.
- `parent` is not `merged_into`. Two self-FKs on one model, meaning opposite things:
  one is containment, the other records where a retired subject's documents and
  followers went. Distinct `related_name`s and a comment saying so, or somebody
  reads one as the other.

## A branch with no RFCs

Nothing in the model objects to a subject with no assignments — that is what every
subject looks like the moment staff create it. The trouble is entirely in what the
reads say about it, and it shows up in four places.

The detail read renders `documents: []`. For `messaging` that is not empty, it is
wrong: the RFCs are one level down under `email`, `mime`, `netnews` and `x400`, and
a Red page saying there is nothing on the subject of messaging is a false
statement Reef published.

A subscription to it matches nothing and says nothing about that. This codebase
works hard to avoid exactly that failure — retiring rather than deleting so a
follower is not cut off, `PROTECT` on `Subscription.subject`, a notification when a
merge changes what somebody's subscription means. A subscription that was dead the
day it was created would be out of character.

The statistics already exclude it. `stats/api.py` filters
`subject__assignments__isnull=False`, so an empty parent contributes nothing to
`subscriber_count` even for the readers following it.

And a picker needs the node regardless, or the tree cannot be drawn, so it has to
appear in the list read either way.

Rolling up answers all four at once, which is why it is the first decision.
Assignments stay exactly where column D puts them, on the leaf, and coverage is
computed on read:

    messaging                             0 direct — a container
    |-- email                             has both
    |   |-- smtp                          leaf
    |   |-- imap                          leaf
    |   \-- email-authentication          0 direct — a container
    |       |-- dkim                      leaf, depth 4
    |       |-- spf                       leaf
    |       \-- dmarc                     leaf
    |-- mime
    |   \-- media-types
    \-- netnews
        \-- nntp

    Stored: an assignment on dkim, and nothing else.
    Read:   dkim, email-authentication, email and messaging all cover it.

The alternative — a strict reading where an assignment means only the subject named
and a parent is navigation — is coherent, and it is what the code does today. It
leaves the four consequences above in place and makes a third of the vocabulary
unusable as anything but a heading.

No `assignable` flag, for now. The draft has curators assigning to
`security / cryptography` and not to `messaging`, so a flag forbidding interior
assignment would be wrong for the first and right for the second, and nobody can
tell which without having curated it. A picker that wants to steer readers toward
leaves can read `document_count == 0 and children` off the payload. Add the column
if curators ask for it, not in anticipation.

## Roll-up belongs in one place

The risk is not the SQL. It is that four call sites independently perform the same
one-hop join today, and only one of them will be updated:

- `subscriptions/matching.py:90`, `subject__assignments__doc__in=docs`. If this
  drifts, followers of a parent get no mail. The visible bug.
- `stats/api.py:90`, pairs of `subject__assignments__doc` and `user_id`. If this
  drifts, `subscriber_count` disagrees with who was actually mailed.
- `surveys/audience.py:100`, `subject__slug__in` unioned with alias slugs. If this
  drifts, targeting `messaging` reaches nobody, indistinguishable from targeting an
  empty subject.
- `subjects/serializers.py:45`, `obj.assignments.all()`. If this drifts, empty
  branch pages in Red.

So the first thing built after the model is a module the four of them share, pure
functions over the `path` column and no ORM cleverness at the call sites:

    # subjects/tree.py
    covering_subject_ids(docs)   # every subject covering these documents,
                                 #   ancestors included
    documents_under(subject)     # the subtree's documents, deduplicated
    descendant_ids(subject)      # one path__startswith
    ancestors_of(subject)        # read off the path string, no query

`covering_subject_ids` is the load-bearing one and stays two queries at any depth:
fetch the paths of the subjects the document is directly assigned to, then resolve
those paths' prefixes to ids. No recursion, and the existing single `Q`-OR in
`matching.py` survives intact — the subject branch becomes
`Q(kind=SUBJECT, subject_id__in=covering)`.

That mirrors something already in that function. `subscriptions_for_document` opens
with `docs = [doc, *rfcmeta.containing_subseries(doc)]`, expanding one changed
document into every container a subscriber could have named. A parent subject is
one more such container, expanded on the other axis, and the module docstring's
argument for the first covers the second without amendment.

`.distinct()` stays essential and gains a second reason: a parent can now reach one
document through several children.

## Curating a tree in the admin

Curation happens in the admin and stays there. Six changes, in the order of how
much each buys.

Seeing the tree is the cheapest and the largest. Order the changelist by `path`
rather than `name` so children sit under their parents, indent the name by `depth`,
and show the path as its own column. It is close to a one-line change and it is the
difference between several hundred names and a browsable vocabulary. It also makes
mis-filing visible, which matters more than it sounds — see the import section.

Picking a parent needs `autocomplete_fields = ["parent"]`, with `search_fields`
extended to `path` so that typing `messaging/` narrows to that branch. The
autocomplete labels rows by `__str__`, which is the bare name, and that is
ambiguous exactly where it hurts: `send` says nothing about which branch it belongs
to. Override the label in a `SubjectAdminForm` (`label_from_instance` returning the
path) rather than changing `__str__`, which is also read by the merge notice and the
mail template.

Adding a child gets two gestures, both cheap and both the natural one: an "add
child" link per changelist row opening the add form with `?parent=<pk>` prefilled,
which Django honours as initial data, and a `children` inline on the change page (a
self-FK inline with `fk_name="parent"`) so a parent shows what is under it and can
grow one more.

Moving a subtree gets a bulk action with an intermediate confirmation page carrying
a parent picker: transactional, cycle-checked, depth-checked against the deepest
descendant, and rebuilding `path` for everything it moved. No drag-and-drop; that is
what the tree libraries are for, and the dependency is not worth an operation that
happens a few times a year.

The assignment admin needs its filter replaced. `list_filter = ["subject"]` renders
a link per subject; a `SimpleListFilter` over the fourteen roots, matching on path
prefix, plus `subject__path` in `search_fields`, is fourteen links and a search box
instead of several hundred links.

And the mistakes are refused in the form rather than described in a comment: cycles,
depth overflow, and a retired parent. The admin already reports a `PROTECT` refusal
legibly, which is the precedent.

## Reads

Everything goes in one large `subjects.json`, because Red is a single-page
application that fetches JSON per route and needs the titles to render the page.
Resolving identifiers against Red's own data would be a second round trip, which is
the thing the precomputed files exist to avoid. So one fetch carries the vocabulary,
the tree, every assignment and every title.

    {
      "documents": {
        "rfc6376": { "title": "...", "subseries": [] },
        ...
      },
      "subjects": {
        "dkim": {
          "name": "...", "description": "...",
          "parent": "email-authentication",
          "path": "messaging/email/email-authentication/dkim",
          "children": [],
          "documents": ["rfc6376", ...],
          "document_count_deep": 11
        },
        ...
      }
    }

Both halves are objects keyed by identifier and by slug, so that a caller looks a
document or a subject up directly and never builds an index of its own or scans an
array. That is also the answer to referencing documents by array position, which
would be more compact and would force exactly the lookup table it was meant to save:
a position is only useful once something has been built to resolve it. Keying is
slightly smaller than an array of objects in any case, since the key stands in for a
`slug` field.

`children` is carried although it is the inverse of `parent`, because without it
walking down means scanning every subject asking whose parent this is. It is six
hundred slugs, a few kilobytes, and it makes a hop O(1) in both directions —
upwards being free already, since the ancestors are the prefixes of `path`.

Metadata is carried once, in the `documents` map, and referenced by identifier from
the subjects that hold it. Embedding it per subject the way `subjects/<slug>.json`
does would repeat each title once per covering subject, about three times at this
tree's average depth, turning some 9,800 titles into sixty thousand. Carrying it
once is also the argument for one file rather than a file per branch: per-branch
files would duplicate every title across each of its ancestors' files, which is the
same multiplication paid in the store instead of in one payload.

Both counts are carried, and `depth` is not. The counts are derivable — one is the
length of the array beside it, the other needs a subtree walk — but they measure at
about 7KB compressed for the pair, which is less than the argument costs. `depth` is
`path` split on the separator at the point of use. The subtree document lists are
not baked, for the reason they never were: writing them out stores every identifier
once per ancestor, taking some nineteen thousand assignments to sixty thousand
entries, in exchange for a walk that is already O(1) per hop.

Both maps are emitted in sorted order, subjects by `path` and identifiers ascending
within a subject. That is determinism, which this codebase already wants from a
precomputed file, and it is also what makes the file compress, since `rfc9110,
rfc9111, rfc9112` share prefixes and a subject sits beside its siblings.

### Size

Measured on a synthetic file of the shape above: 9,800 documents, 615 subjects
across the five depths, 18,808 assignments at a mean of 1.92 per document, titles
averaging 54 characters, serialised the way `_reserialize` does it with compact
separators.

    whole file            1210 KiB raw     147-260 KiB gzip-6
      documents map        881 KiB raw     roughly 55% of the compressed total
      subjects map         330 KiB raw          67 KiB gzip-6

So about 1.2MB in the bucket and something in the region of 200-250KB over the wire,
which is an ordinary page-load cost for a file that serves a whole section of the
site and is cached across its routes.

The range on the compressed figure is not measurement error in the arithmetic, it is
the one thing a synthetic file cannot settle: how compressible real RFC titles are.
Titles built by recombining a small stock of phrases compress at 11x, which flatters
them; titles built from a uniform draw over a four-thousand-word vocabulary compress
at 3x, which is unfairly harsh on prose that reuses "Protocol", "Extensions for" and
"IPv6" as heavily as this series does. Real titles sit between, and the whole-file
figure moves between about 150KB and about 260KB with them. Nothing else in the file
moves it by more than a few kilobytes.

Identifiers stay in their string form, `"rfc9110"` rather than `9110`, and the
measurement is what settles it: numeric identifiers take 10% off the raw file and
2.6% off the wire, 3.9KB, because the `rfc` prefix recurs some twenty-eight thousand
times and is therefore the most compressible thing in the file. What that 3.9KB would
buy is a payload inconsistent with every other one Reef publishes — `stats.json`
carries `doc`, `ratings/<doc>.json` is keyed by the identifier, `popularity.json`
carries `rfc` — so Red would convert whenever it joined subject data to rating or
stat data. And it would put back the ambiguity the codebase went out of its way to
remove: `DOC_SERIES` holds rfc, bcp, std and fyi, `bcp14` and `rfc14` are different
documents, `SubjectAssignment.save` rejects a bare number with a test naming that
reason, and `matching.py` expands `rfc2119` to `bcp14`. The draft happens to carry
only RFCs; the model permits a BCP, and the first one a curator assigns leaves the
file either unable to say so or carrying a `number | string` union. This would have
been a decision about the publication format rather than about storage, since
`normalize_doc_id` is unaffected either way, but it is not one worth 3.9KB. The only
numeric shape that keeps the series unambiguous is one map per series, which
complicates every lookup and saves the same 3.9KB.

Three things fall out of the measurement, and two of them contradict earlier drafts
of this section.

Titles are 73% of the raw file and only about half the compressed one, because
English prose gzips at 5 to 11x while the identifier references manage 4.9x. An
earlier draft argued for dropping them on the strength of the raw figure; the saving
compressed is 50-120KB, which is not worth a second fetch on every route.

Descriptions are free. A hundred characters of description on every subject costs
57KB raw and 1.6KB compressed, so they can be written properly rather than kept
terse.

And compression tuning is noise at this scale: gzip-9 beats gzip-6 by 3%. Which is
the measured form of the argument for leaving the codec to ordinary CDN negotiation
rather than pre-compressing — see the open items.

### The other files

The per-subject files stay as they are. `subjects/<slug>.json` is what a reader
following a published link arrives at, and the only thing that answers for a retired
subject or an alias, whose payload is a redirect stub rather than a subject at all.
It keeps its direct `documents` array and its `document_meta`, because one document
page fetching one file is the case that argument was made for. What it does not gain
is the subtree, which is the index file's business now.

`stats.json` does not change shape. Its `subscriber_count` for a document simply
rises to include the readers who followed an ancestor of a subject it carries.

There is deliberately no `subjects-tree.json`. The tree is `parent`, `children` and
`path` on the one map, and a second file would be a second thing to keep consistent
with the first.

Keys stay flat, `subjects/<slug>.json`, because slugs stay globally unique. Nesting
files by path would break the precomputer's `owns` regex, which matches exactly one
segment, and the failure is quiet: the purge silently stops deleting stale subject
files.

Enumeration needs no change. `precomputer/registry.py` already sweeps
`Subject.all_objects`, so every node at every depth already gets a file.

`subjects.json` now carries assignments, so it is rebuilt by an assignment change and
not only by a vocabulary change. The signals already rebuild the whole subjects task
on any of the four models and the sixty-second debounce absorbs a curation session,
so nothing new is needed — but the index file is no longer cheap to render, which is
the argument for the single-pass roll-up in the next section rather than a query per
subject.

`?doc=` on the live endpoint should keep returning the subjects actually assigned.
`internet-layer` on every IPv6 RFC is noise, and a caller can derive the breadcrumb
from `path`, which it already has.

The contract is regenerated with
`REEF_DEPLOYMENT_MODE=build ./manage.py spectacular --file reef_api.yaml --validate`,
and the key layout in `precomputer/README.md` is updated.

## What each query costs

Worth settling explicitly, because the naive implementation of every read below is
pathological and the good one is not much more code.

The public reads are not queries. `subjects.json`, `subjects/<slug>.json` and
`stats.json` are all precomputer tasks, rendered to the S3-compatible bucket and
read out of it by Red or a CDN. Nothing a reader does triggers any of this. So the
cost question is about what runs inside a precompute run, plus the one path that is
not precomputed at all.

**The ingest path is the only hot one, and roll-up makes it cheaper rather than
dearer.** `subscriptions_for_document` runs per change event and is not precomputed.
`covering_subject_ids` is two indexed queries returning a handful of rows: the
assignments for the document, where `doc` is already indexed and a document carries
a few subjects at most, and then those subjects' path prefixes resolved to ids,
which at depth four is at most four per assignment. The existing `Q`-OR then gets
`subject_id__in` over roughly twenty ids, which is an index scan on the subscription
table. What that avoids is the shape the ORM invites —
`subject__children__children__assignments__doc__in=docs`, a four-way self-join
written out once per level — and avoiding it is a better argument for the `path`
column than the admin ordering is.

**Inside a precompute run the risk is N+1, not any single query.** Written naively,
each of several hundred subjects gets its own subtree query, and the run does six
hundred round trips before it starts on `document_meta`. Instead one query fetches
every `(subject.path, assignment.doc)` pair — of the order of twenty thousand rows
for roughly 9,800 RFCs at the draft's tagging density — and one pass in Python adds
each document to the set held by each of its at most four ancestors. Eighty thousand
set insertions, which is milliseconds, and every subject's direct list, subtree list,
direct count and deep count all fall out of the same pass. One query per run, not one
per subject.

**`stats.json` is the one that would actually hurt.** `subscriber_count` currently
reads `values_list("subject__assignments__doc", "user_id")`, which returns one row
per subscription per assignment. That is fine against a small vocabulary. Under
roll-up it becomes subscribers times subtree size, so one reader following
`internet-layer` produces a row per RFC beneath it, and Postgres performs that
multiplication and ships the result over the wire. The fix is the same pass again:
fetch `(subject_id, user_id)` pairs, which is one row per subscription rather than
per document, and do the cross product in Python against the closure already in
memory. The rule this is an instance of: never ask the database to materialise a
product that a dictionary lookup can do.

**Survey audiences stay cheap** because they are precomputed too and resolve a named
slug list rather than the whole vocabulary — one prefix range scan per subject named.

One index detail worth not discovering in production. `path__startswith` compiles to
`LIKE 'messaging/%'`, and in Postgres a plain B-tree on a text column is not used for
a prefix match unless the database is in the C collation or the index is declared
with `varchar_pattern_ops`. As the design stands that may never matter: ancestors are
read off the path string in Python and resolved by equality, and descendants are only
wanted in the precompute pass, which does not use a prefix query at all. So the
`varchar_pattern_ops` index is a decision to take deliberately if a prefix query ever
lands on a hot path, rather than a thing to be quietly relying on.

Deep counts and subtree lists are computed on read and not stored. A stored count
would need invalidating on every assignment write, every reparent and every retire,
which is three new ways to be wrong in exchange for saving a pass that costs
milliseconds. The precompute run is already the cache; a second one inside it is not
worth its invalidation.

## Lifecycle

Retiring refuses a node with live children, and offers "retire subtree" as an
explicit action. The alternative, quietly reparenting the children to their
grandparent, restructures the vocabulary as a side effect of removing one node,
which is not what the button says.

Merging needs a `_move_children` step beside `_move_assignments` and `_move_aliases`,
or the source's children dangle under a retired parent. It also has to refuse
merging a subject into its own descendant, which would file `messaging` under
`messaging / email`. Both are cheap, and both are silent corruption if skipped.

Deleting keeps `PROTECT`, matching `Subscription.subject`. An unfollowed leaf created
by mistake still deletes, which is the case deletion exists for.

Existing followers broaden once roll-up ships: a subscription to a subject that
later acquires children covers more than it did when it was made. Compare with a
merge, which notifies. Landing roll-up before the bulk import avoids the question
altogether, because at that moment no subject has children and nothing changes under
anybody. That ordering is the reason the importer is last.

And `templates/subscriptions/mail/_subscription_phrase.txt` says "changes to
anything on the subject of {{ name }}", which for a parent is now too narrow. "Or
anything filed under it" is the missing clause.

## Loading the draft

A management command, `manage.py import_subjects <csv>`: column E builds the tree
and creates missing ancestors, column D attaches the assignments, and names are
derived from slugs (title-cased, edited afterwards in the admin). Dry-run by
default, because this is a curation act over roughly 9,800 documents and it should
have to be confirmed.

This also answers a standing open item, which records that assigning subjects at
scale has no source, and that deriving assignments from keywords "would be a guess
dressed as data". The draft is a curated source, which is what was missing.

The report is half the deliverable, because the draft carries collisions that a flat
vocabulary hides and a tree makes obvious. Short slugs have been matched to the
wrong branch:

    RFC 779   Telnet send-location option
              -> internet-layer / ipv6 / neighbor-discovery / send
    RFC 6955  Diffie-Hellman Proof-of-Possession Algorithms
              -> messaging / email / pop3
    RFC 9271  Uninterruptible Power Supply Management Protocol
              -> real-time-communications / nat-traversal / turn
    RFC 8227  MPLS-TP Shared-Ring Protection for Ring Topology
              -> messaging / instant-messaging / msrp

None of these is a modelling problem; they are what happens when a bare tag is
matched by string. They are also the argument for showing `path` everywhere in the
admin: a curator who sees `messaging / email / pop3` beside a Diffie-Hellman RFC
catches it, and one who sees `pop3` does not.

So the command prints: nodes created, assignments created, tags in column D with no
path in column E, any path deeper than four, and — as a hard failure rather than a
warning — any leaf slug appearing under two different paths, since global uniqueness
depends on there being none.

## The schema Red validates against

Reef already does this in the other direction, and the arrangement should be
symmetric. `reef/schemas/rfc-mini-index.schema.json` describes Red's index: Red owns
the data, Reef holds a copy, and Reef enforces it with
`jsonschema.Draft202012Validator` in `reef/rfcmeta.py`. Reef owns `subjects.json`, so
the schema for it lives in Reef, Reef validates against it before uploading, and Red
holds a copy.

    precomputer/schemas/subjects.schema.json    authored and committed in Reef
      -> validated against the rendered payload in the run, before put()
      -> copied by hand into Red
      -> json-schema-to-zod at Red's build time
      -> the Zod schema Red validates the fetched file with at runtime

Not `reef/schemas/`, whose README defines that directory as data Reef reads from
somewhere else. These are files Reef writes, so they sit beside the registry that
produces them, with a README of their own recording the sync in the same terms the
existing one uses for the traffic going the other way.

### The file is the source, not an export

Plain JSON Schema, Draft 2020-12 — the draft `rfcmeta` already validates with —
hand-authored and committed. It is deliberately not generated from anything on the
Python side, and the reason is the consumer: the artifact has to be language
agnostic, because Red reads it too, so making it an export of a Pydantic model or of
the DRF serializers would put a Python-shaped step between Reef and a file whose
whole job is to be neutral. It would also add a dependency and a staleness test
between the model and the file, in exchange for nothing Red can use.

`reef_api.yaml` is not a candidate for the same reason twice over. drf-spectacular
emits OpenAPI 3.0.3, whose schema objects are not JSON Schema — nullability is
spelled `nullable: true` rather than as a type union, and there are no `$defs` — so
it would need converting; bumping `OAS_VERSION` to 3.1 would fix that and rewrite the
contract the Nuxt client and Red both already generate from. And the endpoint no
longer returns what the file contains in any case: `subjects.json` is two keyed maps
carrying titles, the endpoint returns a list without them, because Reef holds no
document metadata and resolves it only at precompute time.

What a hand-authored schema would normally cost is drift, the file describing what
somebody believed the builder emitted. That is answered by where it is enforced
rather than by a generator, and answered better: the run validates the rendered
payload on every execution, so a builder that drifts fails the next run rather than
whenever a test is remembered.

### Where it is enforced

In the run, between rendering and `put`, and a failure fails the run without
uploading. The previous payload then stays in the bucket, which is the posture
`LocalBlobStore.put` already takes for a crash by writing beside the target and
moving it into place. Publishing something Red will reject takes Red's page down;
declining to publish leaves it one run behind.

### Permissive as a consumer, strict as a producer

The committed file carries no `additionalProperties: false`, and Red validates with
it exactly as committed. `reef/schemas/README.md` already argues this from the other
side — rejecting unknown keys "would turn every field Red adds into a Reef outage" —
and Reef is the producer now, so it owes Red the same additive guarantee it is given:
new keys may appear, existing ones do not change meaning or go away. One artifact,
permissive, copied verbatim, and no strict variant in the repository that could one
day be the copy that gets sent.

Reef's own use of that file is strict, and nothing about the file has to change for
it to be, because `jsonschema` validates against a schema *object*. The run loads the
permissive schema, tightens it in memory, and builds the validator from the result:

    schema = schemas.load("subjects")            # what Red gets, verbatim
    validator = jsonschema.Draft202012Validator(schemas.strict(schema))
    validator.validate(payload)                  # before put(), never after

Reef generates this payload, so a key Reef did not mean to write is a bug in Reef
rather than someone else's additive change, and it should stop the run.

`strict()` has one rule that matters and one failure mode worth guarding. The rule:
set `additionalProperties: false` only on an object subschema that declares
`properties` and carries neither `additionalProperties` nor `patternProperties` of
its own, and recurse into those where they exist. Injecting it everywhere would break
the `documents` map, since a map with arbitrary keys is typed precisely by giving
`additionalProperties` a schema — `{"type": "object", "additionalProperties": {...}}`
— and overwriting that with `false` would forbid every document rather than
constrain it. The walk should also fail loudly on a keyword it does not recognise,
rather than pass it through untightened, because a composition keyword nobody taught
it about is a branch where the strictness silently stopped applying.

The failure mode is that same silence at a larger scale: a `strict()` that returned
its argument unchanged would look exactly like a `strict()` that worked, for as long
as the payload happened to be correct. So the tests are not that a good payload
validates — that is the run's job — but that a payload with one stray key is
rejected, and that a payload whose map keys are ordinary documents still passes.
Without the first, the tightening can regress to a no-op and nothing notices.

Whether strictness should also mean requiring every declared property is a separate
knob and a separate decision. It would catch an omission as well as an addition,
which is the other half of the same class of bug, and it needs the schema to declare
optionality honestly first.

### Consequences

Two, both pre-existing and both surfaced by this. The precomputer's byte-equality
invariant — the test that strips a task's additions and compares what is left against
the view's own bytes — does not hold for a task whose payload is not the view's
output at all, so this task needs a different assertion. And the additions on every
other task, `document_meta` on a subject file and the `_meta` block on stats and
popularity rows, are in no contract today; they should acquire schemas by the same
route, which is why the directory is named for the files rather than for this one.

The change from the current shape is not additive: `subjects.json` goes from a list
to two keyed maps, so anything reading today's file breaks. That needs coordinating
with Red rather than shipping, and it is the only breaking item in this plan.

## Implementation steps

Each step ends with a commit.

1. Model and invariants: `parent`, `path` and `depth` on Subject; cycle, depth and
   retired-parent validation in `clean()` and `save()`; `PROTECT`; path recomputation
   on save, cascading to descendants; a `rebuild_paths` management command; a test
   asserting stored equals derived for every row. No behaviour change anywhere else —
   every existing subject becomes a root, and the migration is additive and
   reversible. Commit: "feat: hierarchical subjects".
2. The tree helper: `subjects/tree.py`, pure functions over `path`, unit-tested
   against a fixture tree four deep. Nothing calls it yet, which is the point: it
   exists before the four callers need it, so none of them invents its own. Commit:
   "chore: add subject tree helpers".
3. Lifecycle: retire-with-children refusal and a retire-subtree action;
   `_move_children` and the merge-into-descendant refusal in `merge.py`; the missing
   clause in the mail phrase. Commit: "feat: retire and merge subjects with children".
4. Reads: `parent`, `path` and `children` on the serializers, tree ordering on the
   list. In the precomputer, `subjects.json` becomes the index file: two keyed maps,
   sorted, metadata carried once, nothing derivable in constant time. Regenerate
   `reef_api.yaml`; update the key layout in `precomputer/README.md`. A test on the
   rendered size, so that metadata creeping back in per subject fails loudly rather
   than quietly costing several megabytes. Commit: "feat: publish the subject tree".
5. The schema: `precomputer/schemas/subjects.schema.json`, authored, committed and
   permissive, plus `schemas.load` and `schemas.strict`; validation of the rendered
   payload in the run before `put`, tightened, failing the run rather than uploading;
   tests that a stray key is rejected and that map-typed objects still pass, so the
   tightening cannot regress to a no-op unnoticed; and a README beside it recording
   the sync into Red and the derivation of Zod there. Commit: "feat: validate
   precomputed subjects against a published schema".
6. Roll-up at the four callers, all through `subjects.tree`. `stats/api.py` changes
   shape as well as breadth: it fetches `(subject_id, user_id)` pairs and does the
   cross product in Python, rather than letting the join multiply subscribers by
   subtree size. Tests that a document assigned only to a depth-four leaf notifies a
   subscriber to its root, that `subscriber_count` agrees with the mail that went
   out, that a survey audience naming a parent resolves its subtree, and a query
   count over the precompute pass that fails if it grows with the vocabulary.
   Commit: "feat: a parent subject covers its descendants".
7. Admin UX: tree ordering and indentation, the path column, autocomplete parent with
   path labels, the children inline, the add-child link, the move-subtree action, and
   the assignment filter over the fourteen roots. Commit: "feat: curate subjects as a
   tree".
8. Importer, then the data: `manage.py import_subjects`, dry-run by default, ancestors
   auto-created, collisions reported and duplicate leaf slugs refused, and an explicit
   precompute rebuild since bulk writes fire no signals. Then load the draft and read
   the report before committing the result. Commit: "feat: import subject assignments".

## Open items

- Pre-compressing what goes to the bucket, as a change to `S3BlobStore.put` covering
  every payload rather than anything specific to subjects. `put` sets `ContentType`
  and no `ContentEncoding` today, so the codec is whatever the CDN negotiates at
  whatever level it chooses. Storing brotli at quality 11 with `ContentEncoding: br`
  would take control of that, and the measurement above says the prize is modest —
  gzip-9 beats gzip-6 by 3% here, so brotli is plausibly 15-25%, forty-odd kilobytes
  on this file. Against that it costs a dependency, since Python 3.13 has brotli
  neither in the standard library nor zstd, and it is unconditional: R2 returns the
  encoding header whether or not the request advertised it, so anything reading the
  bucket outside a browser has to decompress. Worth doing across the board at some
  point, not worth doing for this.
- Whether `subjects.json` needs splitting once measured. The estimates here are
  arithmetic over an assumed tagging density and want replacing with a real number at
  step 8. If it is wrong by an order of magnitude, the reserve is to move the
  `documents` map into a file of its own, so that a route needing only the tree does
  not fetch the titles.
- Assignment as an event, already an open item, gets wider. Assigning `smtp` would
  now be news to followers of `messaging`, if that event is ever wired at all.
- Interior assignment policy. The draft assigns to some branch nodes and not others,
  and nothing decides whether that is intended. Left to curation until it hurts.
- Aliases and paths. An alias resolves to a subject and therefore to a branch, and
  nothing yet says whether a survey audience naming an alias of a parent should reach
  that parent's subtree. It falls out of the roll-up decision, but should be written
  down rather than inferred.
- Reparenting and published links. A rename leaves an alias behind; a move leaves
  nothing, because the slug did not change and the URL still resolves. Worth
  confirming that Red does not build URLs from `path`.
