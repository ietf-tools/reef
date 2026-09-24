# Copyright The IETF Trust 2026, All Rights Reserved
"""Mirror the vocabulary and assignments from rfc-editor/rfc-subject-tags.

Two files, two purposes, both fetched fresh every run:

- taxonomy.yaml is the curated vocabulary -- one entry per tag, with its id,
  uuid, slug, parent and description. It becomes Reef's Subject tree. Everything
  else in an entry (kind, match rules, groups, implies, decomposes_to, yields_to,
  stats) is how *they* compute assignments or serve a two-axis search Reef has
  no use for, and is read and discarded.
- rfc-tags.json is a separate, generated artifact (rebuilt daily by their CI,
  published to GitHub Pages) carrying the per-RFC output of running their
  matching engine against taxonomy.yaml. Its `tags` field, the leaf assignment,
  becomes Reef's SubjectAssignment rows; its `paths` field is their derived
  ancestor closure, which subjects.tree.rollup() already computes from Reef's
  own `path` column, so it is never read.

A tag carries three names, and each maps to one thing in Reef. The id is written
the way the documents write the term (HTTP/2, S/MIME, congestion control) and is
what `parent` and rfc-tags.json refer to; it becomes Reef's `name`. The slug is
their slugify of the id and becomes Reef's `slug`. The uuid never changes, even
when a rename changes the other two, so it is what a sync matches on and is kept
as `upstream_uuid`. A subject not yet linked to a uuid is matched by slug, then
by name, and linked on that run.

A sync is a mirror: it overwrites `name`, `slug`, `description` and `parent`
even where staff hand-edited them, and it removes what the source no longer has.
A name the vocabulary needs that a subject outside it still holds -- retired, or
retiring on this run -- is taken from that subject, which becomes "name (slug)":
a retired subject's name is published nowhere, and the one in the vocabulary is
what readers see.

Removal is two different operations, because the domain already draws this
line. A subject that vanishes is retired, never deleted -- Subscription.subject
is on_delete=PROTECT specifically so a cascade can never silently stop
somebody's mail, and retire() already exists to take a subject out of the
picker while its subscribers go on matching. An assignment that vanishes is
hard-deleted -- SubjectAssignment carries no subscriber of its own, and its own
docstring is explicit that there is no state between assigned and not.

Manual only. There is deliberately no scheduled task calling this: a mirror of
an external taxonomy that nobody is watching is exactly the kind of change that
should have a person looking at the result each time.
"""

import json
import logging
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import yaml
from django.core.exceptions import ValidationError
from django.core.management import CommandError
from django.core.validators import validate_slug
from django.db import transaction
from django.db.models import CharField, Count, Value
from django.db.models.functions import Cast, Concat

from reef.docids import normalize_doc_id
from reef.locks import advisory_lock

from .models import (
    MAX_DEPTH,
    NAME_MAX_LENGTH,
    SLUG_MAX_LENGTH,
    Subject,
    SubjectAlias,
    SubjectAssignment,
)

logger = logging.getLogger("reef")

TAXONOMY_URL = (
    "https://raw.githubusercontent.com/rfc-editor/rfc-subject-tags/main/taxonomy.yaml"
)
RFC_TAGS_URL = "https://rfc-editor.github.io/rfc-subject-tags/rfc-tags.json"

# Generous headroom, not a real limit: rfc-tags.json is 3.4 MB today. Defense in
# depth against a redirect to something unexpected rather than a size anyone
# expects to hit.
MAX_FETCH_BYTES = 20_000_000

CATCH_ALL_IDS = {"misc", "other", "general"}

LOCK_NAME = "subjects.sync"

# A candidate only clears the bar with real evidence behind it: meaningful
# document overlap, or a strong textual resemblance backed by at least some
# overlap in the name. Never a guess dressed as a score.
SUGGESTION_JACCARD_THRESHOLD = 0.2
SUGGESTION_DESCRIPTION_THRESHOLD = 0.6
SUGGESTION_SLUG_THRESHOLD = 0.4
SUGGESTION_MAX_PER_SUBJECT = 3


def displaced_name(subject):
    """The name a subject outside the vocabulary gives up its own for: unique
    because the slug is, and still recognisably the same subject in the admin."""
    suffix = f" ({subject.slug})"
    return f"{subject.name[: NAME_MAX_LENGTH - len(suffix)]}{suffix}"


def title_case(slug):
    """A placeholder name for a subject created from nothing but a slug, to be
    edited by whoever curates it next.

    Deliberately mechanical: a guessed expansion of an initialism would be worse
    than an obvious placeholder. The sync does not need it, since a tag's id is
    already written the way the documents write it.
    """
    return slug.replace("-", " ").title()


@dataclass
class SubjectChange:
    slug: str  # the slug after this change
    changes: dict  # field -> (old, new)
    pk: int | None = None
    parent_uuid: str | None = None


@dataclass
class VocabularyDiff:
    # [{"uuid", "slug", "name", "parent_uuid", "description"}], shallowest first
    to_create: list
    to_update: list  # [SubjectChange]
    to_unretire: list  # [slug], as it will be after to_update
    to_retire: list  # [Subject], live subjects no tag matched
    # [SubjectChange], subjects no tag matched moved off a name a tag's id needs
    displaced: list = field(default_factory=list)
    # The existing subject each matched tag resolves to, so a parent can be
    # found before the subject it names has been linked by the write.
    pk_by_uuid: dict = field(default_factory=dict)
    # Reasons the diff cannot be written as it stands; nothing is, while any exist.
    conflicts: list = field(default_factory=list)


@dataclass
class AssignmentDiff:
    to_create: list  # [(subject_id, doc)]
    to_delete_pks: list  # [SubjectAssignment.pk]
    delete_count: int
    create_count: int
    unresolved: list  # [(slug, doc)] naming no known subject


@dataclass
class Suggestion:
    target_slug: str
    target_name: str
    target_pk: int
    score: float
    jaccard: float
    shared_count: int
    old_count: int
    description_ratio: float


@dataclass
class SyncResult:
    skipped: bool = False
    skip_reason: str = ""
    written: bool = False
    needs_confirmation: bool = False
    # A malformed taxonomy, or slugs the current vocabulary already holds.
    validation_problems: list = field(default_factory=list)
    created: list = field(default_factory=list)  # [slug]
    updated: list = field(default_factory=list)  # [SubjectChange]
    unretired: list = field(default_factory=list)  # [slug]
    retired: list = field(default_factory=list)  # [slug]
    assignments_created: int = 0
    assignments_deleted: int = 0
    unresolved_assignments: list = field(default_factory=list)  # [(slug, doc)]
    top_deltas: list = field(default_factory=list)  # [(slug, old_count, new_count)]
    suggestions: dict = field(default_factory=dict)  # {retired_slug: [Suggestion]}
    retire_count: int = 0
    live_count_before: int = 0
    assignment_total_before: int = 0
    assignment_total_after: int = 0


def _fetch(url, timeout=30):
    """GET url, logging how long it actually took.

    timeout bounds each individual socket operation (connect, and each read),
    not the transfer as a whole -- a connection that keeps dribbling bytes
    without ever going fully idle for `timeout` seconds can still run long
    overall. The elapsed-time log below is what would show that happening,
    since a per-operation timeout wouldn't catch it.
    """
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_FETCH_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.error(
            "subject sync: fetching %s failed after %.1fs: %s",
            url,
            time.monotonic() - started,
            exc,
        )
        raise CommandError(f"Could not fetch {url}: {exc}") from exc
    elapsed = time.monotonic() - started
    if len(data) > MAX_FETCH_BYTES:
        logger.error(
            "subject sync: %s exceeded the fetch cap after %.1fs", url, elapsed
        )
        raise CommandError(f"{url} exceeded the {MAX_FETCH_BYTES}-byte fetch cap")
    logger.info("subject sync: fetched %s (%d bytes) in %.1fs", url, len(data), elapsed)
    return data


def fetch_taxonomy(url=TAXONOMY_URL):
    """The parsed taxonomy.yaml, as a dict with a top-level `tags` list."""
    raw = _fetch(url)
    try:
        # safe_load, never plain load: this is untrusted external content, and
        # plain load can execute arbitrary Python via YAML tags.
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise CommandError(f"{url} is not valid YAML: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("tags"), list):
        raise CommandError(f"{url} has no top-level 'tags' list")
    return parsed


def fetch_assignments(url=RFC_TAGS_URL):
    """The parsed rfc-tags.json, as a dict keyed by RFC id."""
    raw = _fetch(url)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CommandError(f"{url} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CommandError(f"{url} is not a JSON object keyed by RFC id")
    return parsed


def _canonical_uuid(value):
    """value as a lowercase hyphenated uuid string, or None if it is not one."""
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _is_slug(value):
    # The type check first: validate_slug stringifies what it is given, and
    # str(None) is a perfectly good slug.
    if not isinstance(value, str):
        return False
    try:
        validate_slug(value)
    except ValidationError:
        return False
    return True


def validate_taxonomy(taxonomy):
    """Every structural problem in the fetched taxonomy, checked all at once.

    Reef doesn't need most of engine.Taxonomy's own validation -- match rules,
    implies targets and so on are dropped entirely, see the module docstring --
    but the structural rules Reef's own tree also has to hold are checked here,
    the same way seed_subjects.py._check() does it: everything checked first,
    one report naming every problem, nothing written if any of it fails.

    The uuid and slug are checked here even though their loader already refuses
    a file without them: the uuid is what every match runs through and the slug
    goes straight into a unique column and every path beneath it, so a file that
    somehow reached Reef without them has to stop here rather than half-apply.
    """
    problems = []
    by_id = {}
    for entry in taxonomy.get("tags") or []:
        tag_id = entry.get("id")
        if not tag_id:
            problems.append("a tag entry has no id")
            continue
        # safe_load turns an unquoted id that looks like a number into one.
        if not isinstance(tag_id, str):
            problems.append(f"{tag_id!r} is not a string id")
            continue
        if tag_id in by_id:
            problems.append(f"{tag_id!r} appears more than once")
            continue
        by_id[tag_id] = entry

    tag_by_uuid, tag_by_slug = {}, {}
    for tag_id, entry in by_id.items():
        if len(tag_id) > NAME_MAX_LENGTH:
            problems.append(
                f"{tag_id!r} is longer than a subject name can be "
                f"({NAME_MAX_LENGTH} characters)"
            )

        tag_uuid = _canonical_uuid(entry.get("uuid"))
        if tag_uuid is None:
            problems.append(f"{tag_id!r} has no valid uuid")
        elif tag_uuid in tag_by_uuid:
            problems.append(
                f"{tag_id!r} has the same uuid as {tag_by_uuid[tag_uuid]!r}"
            )
        else:
            tag_by_uuid[tag_uuid] = tag_id

        slug = entry.get("slug")
        if not _is_slug(slug):
            problems.append(f"{tag_id!r} has no valid slug (got {slug!r})")
        else:
            if len(slug) > SLUG_MAX_LENGTH:
                problems.append(
                    f"{tag_id!r} has a slug longer than {SLUG_MAX_LENGTH} characters"
                )
            elif slug in tag_by_slug:
                problems.append(
                    f"{tag_id!r} has the same slug as {tag_by_slug[slug]!r}"
                )
            else:
                tag_by_slug[slug] = tag_id
            if slug in CATCH_ALL_IDS:
                problems.append(
                    f"{tag_id!r} is a catch-all tag, which the vocabulary refuses"
                )

        parent = entry.get("parent")
        if parent and parent not in by_id:
            problems.append(f"{tag_id!r} names unknown parent {parent!r}")

    for tag_id in by_id:
        seen = set()
        current = tag_id
        depth = 0
        while current:
            if current in seen:
                problems.append(f"{tag_id!r} sits in a parent cycle")
                break
            seen.add(current)
            entry = by_id.get(current)
            if entry is None:
                break  # already reported above as an unknown parent
            depth += 1
            if depth > MAX_DEPTH:
                problems.append(f"{tag_id!r} is deeper than {MAX_DEPTH} levels")
                break
            current = entry.get("parent")

    return problems


def _depth(tag_id, by_id, cache):
    if tag_id in cache:
        return cache[tag_id]
    parent = by_id[tag_id].get("parent")
    depth = 1 if not parent else _depth(parent, by_id, cache) + 1
    cache[tag_id] = depth
    return depth


def match_subjects(entries, subjects):
    """The existing subject each tag resolves to, as {uuid: Subject}.

    By upstream_uuid first, and only then, for a subject not yet linked to any
    tag, by slug and finally by name -- the name pass is what links a subject
    whose curated name already reads the way the tag's id now does (S/MIME)
    while its slug was derived differently (s-mime against smime). In separate
    passes, so a weaker match can never take a subject a uuid already claims.
    Reads against all_objects: a tag that comes back after its subject was
    retired must bring that subject back, not create a second one.
    """
    linked = {str(s.upstream_uuid): s for s in subjects if s.upstream_uuid}
    unlinked = [s for s in subjects if s.upstream_uuid is None]
    by_slug = {s.slug: s for s in unlinked}
    by_name = {s.name: s for s in unlinked}

    matched, claimed = {}, set()
    for entry in entries:
        tag_uuid = _canonical_uuid(entry["uuid"])
        if tag_uuid in linked:
            matched[tag_uuid] = linked[tag_uuid]
            claimed.add(linked[tag_uuid].pk)
    for lookup, key in ((by_slug, "slug"), (by_name, "id")):
        for entry in entries:
            tag_uuid = _canonical_uuid(entry["uuid"])
            subject = lookup.get(entry[key])
            if tag_uuid in matched or subject is None or subject.pk in claimed:
                continue
            matched[tag_uuid] = subject
            claimed.add(subject.pk)
    return matched


def diff_vocabulary(taxonomy):
    """What apply_vocabulary would create, update, unretire and retire.

    Reads only. A slug this would give a subject that another subject or alias
    already holds is reported in `conflicts` rather than left for the unique
    constraint to refuse halfway through the write. That includes two subjects
    trading slugs, which upstream's slugify can do on its own: it numbers
    duplicates in file order (WHOIS -> whois, WHOIS++ -> whois-2), so reordering
    the file swaps them. Which one keeps the published slug is for a person.
    """
    by_id = {entry["id"]: entry for entry in taxonomy["tags"]}
    depth_cache = {}
    ordered_ids = sorted(
        by_id, key=lambda tag_id: (_depth(tag_id, by_id, depth_cache), tag_id)
    )

    subjects = list(Subject.all_objects.select_related("parent"))
    matched = match_subjects(list(by_id.values()), subjects)
    matched_pks = {subject.pk for subject in matched.values()}
    subject_by_slug = {subject.slug: subject for subject in subjects}
    alias_owner = dict(SubjectAlias.objects.values_list("slug", "subject_id"))
    name_holder = {subject.name: subject for subject in subjects}

    to_create, to_update, to_unretire, displaced, conflicts = [], [], [], [], []
    for tag_id in ordered_ids:
        entry = by_id[tag_id]
        tag_uuid = _canonical_uuid(entry["uuid"])
        slug = entry["slug"]
        parent_entry = by_id.get(entry.get("parent"))
        parent_uuid = _canonical_uuid(parent_entry["uuid"]) if parent_entry else None
        parent_slug = parent_entry["slug"] if parent_entry else None
        description = (entry.get("desc") or "").strip()
        subject = matched.get(tag_uuid)

        holder = subject_by_slug.get(slug)
        if holder is not None and holder is not subject:
            conflicts.append(
                f"{tag_id!r} has slug {slug!r}, which {holder.path!r} already has"
            )
        elif slug in alias_owner and alias_owner[slug] != getattr(subject, "pk", None):
            conflicts.append(
                f"{tag_id!r} has slug {slug!r}, which is already another "
                "subject's alias"
            )

        # A subject matched to another tag gives this name up by taking its own
        # tag's id in the same write, so only one outside the vocabulary has to be
        # moved off it.
        name_taken_by = name_holder.get(tag_id)
        if name_taken_by is not None and name_taken_by.pk not in matched_pks:
            new_name = displaced_name(name_taken_by)
            displaced.append(
                SubjectChange(
                    slug=name_taken_by.slug,
                    changes={"name": (name_taken_by.name, new_name)},
                    pk=name_taken_by.pk,
                )
            )

        if subject is None:
            to_create.append(
                {
                    "uuid": tag_uuid,
                    "slug": slug,
                    "name": tag_id,
                    "parent_uuid": parent_uuid,
                    "description": description,
                }
            )
            continue

        if subject.is_retired:
            to_unretire.append(slug)

        changes = {}
        if subject.upstream_uuid is None:
            changes["upstream_uuid"] = (None, tag_uuid)
        if subject.name != tag_id:
            changes["name"] = (subject.name, tag_id)
        if subject.slug != slug:
            changes["slug"] = (subject.slug, slug)
        # Compared by identity, not slug: a parent renamed in this same run has a
        # different slug but is the same subject.
        new_parent = matched.get(parent_uuid)
        if parent_uuid is None:
            parent_changed = subject.parent_id is not None
        else:
            parent_changed = new_parent is None or new_parent.pk != subject.parent_id
        if parent_changed:
            current_parent_slug = subject.parent.slug if subject.parent_id else None
            changes["parent"] = (current_parent_slug, parent_slug)
        if subject.description != description:
            changes["description"] = (subject.description, description)
        if changes:
            to_update.append(
                SubjectChange(
                    slug=slug, changes=changes, pk=subject.pk, parent_uuid=parent_uuid
                )
            )

    to_retire = [
        subject
        for subject in subjects
        if subject.pk not in matched_pks and not subject.is_retired
    ]
    return VocabularyDiff(
        to_create,
        to_update,
        to_unretire,
        to_retire,
        displaced=displaced,
        pk_by_uuid={tag_uuid: subject.pk for tag_uuid, subject in matched.items()},
        conflicts=conflicts,
    )


#  A subject's save() is not cheap -- validate_tree() walks ancestors, and
#  HistoricalRecords() writes a second row per save -- and to_create/to_update
#  go through it one at a time, in a loop, because path/depth and history both
#  depend on it. bulk_create would skip all of that. This checkpoint interval
#  is what makes a slow run visible in the logs while it's happening, rather
#  than only at the end: if this is where a hang lives, the last logged index
#  and its timestamp says how far it got and how long each batch took.
PROGRESS_LOG_INTERVAL = 50


def apply_vocabulary(diff):
    """Write a VocabularyDiff. Creates and reparents before retiring, so a
    subject moving out of a vanishing branch is safely out of it first.

    A slug change goes through Subject.save() like any other rename, so the old
    slug is left behind as an alias and published links naming it still resolve.
    """
    created_by_uuid = {}

    def resolve_parent(parent_uuid):
        if parent_uuid is None:
            return None
        if parent_uuid in created_by_uuid:
            return created_by_uuid[parent_uuid]
        return Subject.all_objects.get(pk=diff.pk_by_uuid[parent_uuid])

    # Every name this run changes is parked on a placeholder first, so that two
    # subjects trading names, or one taking a name another is giving up, never
    # meet the unique constraint halfway through. Through update() rather than
    # save(): a placeholder is not a state the history should record.
    renaming = [
        change.pk
        for change in [*diff.displaced, *diff.to_update]
        if "name" in change.changes
    ]
    Subject.all_objects.filter(pk__in=renaming).update(
        name=Concat(Value("subject-sync-placeholder-"), Cast("pk", CharField()))
    )
    for change in diff.displaced:
        subject = Subject.all_objects.get(pk=change.pk)
        _, subject.name = change.changes["name"]
        # update_fields, so that a retired subject under a retired parent is not
        # put through validate_tree(), which refuses exactly that.
        subject.save(update_fields=["name", "updated_at"])
        logger.info("subject sync: moved %s off its name", change.slug)

    started = time.monotonic()
    total = len(diff.to_create)
    for index, item in enumerate(diff.to_create, start=1):
        subject = Subject.objects.create(
            upstream_uuid=item["uuid"],
            slug=item["slug"],
            name=item["name"],
            description=item["description"],
            parent=resolve_parent(item["parent_uuid"]),
        )
        created_by_uuid[item["uuid"]] = subject
        logger.info("subject sync: created %d/%d: %s", index, total, item["slug"])
        if index % PROGRESS_LOG_INTERVAL == 0 or index == total:
            logger.info(
                "subject sync: %d/%d subject(s) created, %.1fs elapsed",
                index,
                total,
                time.monotonic() - started,
            )

    started = time.monotonic()
    total = len(diff.to_update)
    for index, change in enumerate(diff.to_update, start=1):
        subject = Subject.all_objects.get(pk=change.pk)
        if "upstream_uuid" in change.changes:
            _, subject.upstream_uuid = change.changes["upstream_uuid"]
        if "name" in change.changes:
            _, subject.name = change.changes["name"]
        if "slug" in change.changes:
            _, subject.slug = change.changes["slug"]
        if "parent" in change.changes:
            subject.parent = resolve_parent(change.parent_uuid)
        if "description" in change.changes:
            _, new_description = change.changes["description"]
            subject.description = new_description
        subject.save()
        logger.info(
            "subject sync: updated %d/%d: %s (%s)",
            index,
            total,
            change.slug,
            ", ".join(change.changes),
        )
        if index % PROGRESS_LOG_INTERVAL == 0 or index == total:
            logger.info(
                "subject sync: %d/%d subject(s) updated, %.1fs elapsed",
                index,
                total,
                time.monotonic() - started,
            )

    for slug in diff.to_unretire:
        subject = Subject.all_objects.get(slug=slug)
        if subject.is_retired:
            subject.unretire()
            logger.info("subject sync: unretired %s", slug)

    # Only the topmost vanished subject in each branch is retired directly;
    # retire(subtree=True) cascades to its own vanished descendants, so a
    # child would otherwise be asked to retire twice.
    started = time.monotonic()
    retiring_slugs = {subject.slug for subject in diff.to_retire}
    for subject in diff.to_retire:
        parent_slug = subject.parent.slug if subject.parent_id else None
        if parent_slug in retiring_slugs:
            continue
        subject.retire(subtree=True)
        logger.info("subject sync: retired %s (and any live descendants)", subject.slug)
    if diff.to_retire:
        logger.info(
            "subject sync: %d subject(s) marked for retirement, %.1fs elapsed",
            len(diff.to_retire),
            time.monotonic() - started,
        )


def diff_assignments(rfc_tags, taxonomy):
    """What apply_assignments would create and delete, resolved against the
    vocabulary as it stands right now -- call after apply_vocabulary().

    rfc-tags.json names each tag by its id, which is neither a Reef slug nor
    stable across a rename, so each is resolved through the taxonomy fetched in
    the same run to its uuid and from there to the subject linked to it.
    """
    uuid_by_id = {
        entry["id"]: _canonical_uuid(entry["uuid"]) for entry in taxonomy["tags"]
    }
    pk_by_uuid = {
        str(tag_uuid): pk
        for pk, tag_uuid in Subject.all_objects.filter(
            upstream_uuid__isnull=False
        ).values_list("pk", "upstream_uuid")
    }
    current = {
        (subject_id, doc): pk
        for pk, subject_id, doc in SubjectAssignment.objects.values_list(
            "pk", "subject_id", "doc"
        )
    }

    desired = set()
    unresolved = []
    for rfc_id, record in rfc_tags.items():
        try:
            doc = normalize_doc_id(rfc_id)
        except ValidationError:
            unresolved.append((rfc_id, "not a document id"))
            continue
        for tag_id in record.get("tags") or []:
            subject_id = pk_by_uuid.get(uuid_by_id.get(tag_id))
            if subject_id is None:
                unresolved.append((tag_id, doc))
                continue
            desired.add((subject_id, doc))

    to_create = sorted(desired - set(current))
    to_delete_pks = [pk for pair, pk in current.items() if pair not in desired]
    return AssignmentDiff(
        to_create=to_create,
        to_delete_pks=to_delete_pks,
        delete_count=len(to_delete_pks),
        create_count=len(to_create),
        unresolved=unresolved,
    )


def apply_assignments(diff):
    if diff.to_create:
        SubjectAssignment.objects.bulk_create(
            [
                SubjectAssignment(subject_id=subject_id, doc=doc)
                for subject_id, doc in diff.to_create
            ],
            batch_size=2000,
        )
    if diff.to_delete_pks:
        SubjectAssignment.objects.filter(pk__in=diff.to_delete_pks).delete()
    logger.info(
        "subject sync: %d assignment(s) created, %d deleted",
        diff.create_count,
        diff.delete_count,
    )
    if diff.unresolved:
        for slug, doc in diff.unresolved:
            logger.warning(
                "subject sync: %s names no known subject (doc %s)", slug, doc
            )


def _jaccard(a, b):
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def suggest_merges(retiring_slugs, old_docs, old_parent_slug, old_description):
    """Candidate successors for each just-retired subject, scored from document
    overlap and text similarity against every subject now live.

    Run after the sync has committed: old_docs/old_parent_slug/old_description
    are the pre-sync snapshot (captured before anything was written), and the
    candidates' own document sets are read fresh, post-sync.
    """
    if not retiring_slugs:
        # The common case, including every steady-state run: nothing retired,
        # so skip building a full assignment index nothing would use.
        return {}

    started = time.monotonic()
    candidates = list(Subject.objects.select_related("parent"))
    new_docs = defaultdict(set)
    for subject_id, doc in SubjectAssignment.objects.values_list("subject_id", "doc"):
        new_docs[subject_id].add(doc)
    logger.info(
        "subject sync: suggestion index built (%d candidate(s), %d "
        "assignment(s)), %.1fs elapsed",
        len(candidates),
        sum(len(docs) for docs in new_docs.values()),
        time.monotonic() - started,
    )

    suggestions = {}
    for slug in retiring_slugs:
        old = old_docs.get(slug) or set()
        old_parent = old_parent_slug.get(slug)
        old_desc = old_description.get(slug, "")
        scored = []
        for candidate in candidates:
            new = new_docs.get(candidate.pk) or set()
            jaccard = _jaccard(old, new)
            description_ratio = SequenceMatcher(
                None, old_desc, candidate.description
            ).ratio()
            slug_ratio = SequenceMatcher(None, slug, candidate.slug).ratio()
            candidate_parent = candidate.parent.slug if candidate.parent_id else None
            parent_bonus = 1.0 if old_parent == candidate_parent else 0.0
            score = (
                0.6 * jaccard
                + 0.2 * description_ratio
                + 0.1 * slug_ratio
                + 0.1 * parent_bonus
            )
            if jaccard > SUGGESTION_JACCARD_THRESHOLD or (
                description_ratio > SUGGESTION_DESCRIPTION_THRESHOLD
                and slug_ratio > SUGGESTION_SLUG_THRESHOLD
            ):
                scored.append(
                    Suggestion(
                        target_slug=candidate.slug,
                        target_name=candidate.name,
                        target_pk=candidate.pk,
                        score=score,
                        jaccard=jaccard,
                        shared_count=len(old & new),
                        old_count=len(old),
                        description_ratio=description_ratio,
                    )
                )
        scored.sort(key=lambda suggestion: suggestion.score, reverse=True)
        suggestions[slug] = scored[:SUGGESTION_MAX_PER_SUBJECT]
    logger.info(
        "subject sync: suggestions scored for %d retired subject(s), %.1fs elapsed",
        len(retiring_slugs),
        time.monotonic() - started,
    )
    return suggestions


def run_sync(
    vocabulary_url=TAXONOMY_URL,
    assignments_url=RFC_TAGS_URL,
    confirm_large_change=False,
    write=True,
):
    """Fetch, validate, diff, apply and suggest -- the one entry point the
    admin view and the management command both call.

    write=False (the management command's default, like every other importer in
    this app) computes and reports the same diff but rolls back before
    returning, the same way an unconfirmed large change does -- it just reports
    written=False rather than needs_confirmation=True.
    """
    with advisory_lock(LOCK_NAME) as acquired:
        if not acquired:
            logger.info("subject sync: skipped, another run holds the lock")
            return SyncResult(
                skipped=True,
                skip_reason="Another sync is already in progress. Try again shortly.",
            )
        return _run_sync(vocabulary_url, assignments_url, confirm_large_change, write)


def _run_sync(vocabulary_url, assignments_url, confirm_large_change, write):
    run_started = time.monotonic()
    logger.info("subject sync: starting (write=%s)", write)

    # _fetch() itself logs each URL's own elapsed time; this line is the total
    # for both, which is what matters against the request's own time budget
    # (a gunicorn worker will be killed at REEF_GUNICORN_TIMEOUT, 180s by
    # default -- see dev/build/backend-start.sh).
    fetch_started = time.monotonic()
    taxonomy = fetch_taxonomy(vocabulary_url)
    rfc_tags = fetch_assignments(assignments_url)
    logger.info(
        "subject sync: fetched %d tag(s), %d RFC record(s) in %.1fs total",
        len(taxonomy.get("tags") or []),
        len(rfc_tags),
        time.monotonic() - fetch_started,
    )

    problems = validate_taxonomy(taxonomy)
    if problems:
        logger.warning(
            "subject sync: %d problem(s) in the fetched taxonomy", len(problems)
        )
        return SyncResult(validation_problems=problems)

    diff_started = time.monotonic()
    vocab_diff = diff_vocabulary(taxonomy)
    logger.info(
        "subject sync: vocabulary diff: %d to create, %d to update, %d to "
        "unretire, %d to retire, %.1fs elapsed",
        len(vocab_diff.to_create),
        len(vocab_diff.to_update),
        len(vocab_diff.to_unretire),
        len(vocab_diff.to_retire),
        time.monotonic() - diff_started,
    )
    if vocab_diff.conflicts:
        logger.warning(
            "subject sync: %d slug conflict(s); nothing written",
            len(vocab_diff.conflicts),
        )
        return SyncResult(validation_problems=vocab_diff.conflicts)
    live_count_before = Subject.objects.count()
    assignment_total_before = SubjectAssignment.objects.count()
    retire_count = len(vocab_diff.to_retire)

    # Snapshot before anything is written: what a retired subject covered, and
    # what it was called, is otherwise gone the moment its assignments are
    # reconciled away.
    old_docs = {
        subject.slug: set(
            SubjectAssignment.objects.filter(subject=subject).values_list(
                "doc", flat=True
            )
        )
        for subject in vocab_diff.to_retire
    }
    old_parent_slug = {
        subject.slug: (subject.parent.slug if subject.parent_id else None)
        for subject in vocab_diff.to_retire
    }
    old_description = {
        subject.slug: subject.description for subject in vocab_diff.to_retire
    }
    old_counts = dict(
        SubjectAssignment.objects.values("subject_id")
        .annotate(n=Count("doc"))
        .values_list("subject_id", "n")
    )
    slug_by_id_before = {
        subject.pk: subject.slug for subject in Subject.all_objects.all()
    }

    with transaction.atomic():
        apply_started = time.monotonic()
        apply_vocabulary(vocab_diff)
        logger.info(
            "subject sync: vocabulary written, %.1fs elapsed",
            time.monotonic() - apply_started,
        )

        diff_started = time.monotonic()
        assignment_diff = diff_assignments(rfc_tags, taxonomy)
        logger.info(
            "subject sync: assignment diff: %d to create, %d to delete, %d "
            "unresolved, %.1fs elapsed",
            assignment_diff.create_count,
            assignment_diff.delete_count,
            len(assignment_diff.unresolved),
            time.monotonic() - diff_started,
        )
        new_total = (
            assignment_total_before
            - assignment_diff.delete_count
            + assignment_diff.create_count
        )

        needs_confirmation = not confirm_large_change and (
            retire_count > max(5, 0.1 * live_count_before)
            or new_total < 0.5 * assignment_total_before
        )
        if needs_confirmation or not write:
            if needs_confirmation:
                logger.warning(
                    "subject sync: safety threshold tripped (%d retiring, %d -> %d "
                    "assignments); rolled back, awaiting confirmation",
                    retire_count,
                    assignment_total_before,
                    new_total,
                )
            else:
                logger.info("subject sync: dry run, rolled back")
            transaction.set_rollback(True)
            return SyncResult(
                written=False,
                needs_confirmation=needs_confirmation,
                retire_count=retire_count,
                live_count_before=live_count_before,
                assignment_total_before=assignment_total_before,
                assignment_total_after=new_total,
                created=[item["slug"] for item in vocab_diff.to_create],
                updated=[*vocab_diff.to_update, *vocab_diff.displaced],
                unretired=vocab_diff.to_unretire,
                retired=[subject.slug for subject in vocab_diff.to_retire],
                assignments_created=assignment_diff.create_count,
                assignments_deleted=assignment_diff.delete_count,
                unresolved_assignments=assignment_diff.unresolved,
            )

        write_started = time.monotonic()
        apply_assignments(assignment_diff)
        logger.info(
            "subject sync: assignments written, %.1fs elapsed",
            time.monotonic() - write_started,
        )

    # Post-write: suggestions and the delta report both read the now-committed
    # state, and are informational only -- nothing here writes anything.
    retired_slugs = [subject.slug for subject in vocab_diff.to_retire]
    suggestions = suggest_merges(
        retired_slugs, old_docs, old_parent_slug, old_description
    )

    new_counts = dict(
        SubjectAssignment.objects.values("subject_id")
        .annotate(n=Count("doc"))
        .values_list("subject_id", "n")
    )
    deltas = []
    for subject_id, old_count in old_counts.items():
        slug = slug_by_id_before.get(subject_id)
        if slug is None or slug in retired_slugs:
            continue
        new_count = new_counts.get(subject_id, 0)
        if new_count != old_count:
            deltas.append((slug, old_count, new_count))
    deltas.sort(key=lambda item: abs(item[2] - item[1]), reverse=True)

    logger.info(
        "subject sync: done -- %d created, %d updated, %d unretired, %d "
        "retired, %d assignment(s) created, %d deleted, %.1fs total",
        len(vocab_diff.to_create),
        len(vocab_diff.to_update),
        len(vocab_diff.to_unretire),
        len(vocab_diff.to_retire),
        assignment_diff.create_count,
        assignment_diff.delete_count,
        time.monotonic() - run_started,
    )

    return SyncResult(
        written=True,
        created=[item["slug"] for item in vocab_diff.to_create],
        updated=[*vocab_diff.to_update, *vocab_diff.displaced],
        unretired=vocab_diff.to_unretire,
        retired=retired_slugs,
        assignments_created=assignment_diff.create_count,
        assignments_deleted=assignment_diff.delete_count,
        unresolved_assignments=assignment_diff.unresolved,
        top_deltas=deltas[:5],
        suggestions=suggestions,
        retire_count=retire_count,
        live_count_before=live_count_before,
        assignment_total_before=assignment_total_before,
        assignment_total_after=SubjectAssignment.objects.count(),
    )
