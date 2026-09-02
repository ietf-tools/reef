# Copyright The IETF Trust 2026, All Rights Reserved
"""What gets precomputed, and what deliberately does not.

Named registry rather than tasks.py so that "task" means one thing per module: the
Celery tasks that schedule a run live in tasks.py, where celery autodiscovery looks
for them, and the units of precomputing work live here.

One task per public read endpoint. A task yields ``(key, body)`` pairs and
declares, with ``owns``, the keys in the store that are its own, so that a full
run can purge what it no longer produces: a subject that was deleted leaves a
stale ``subjects/<slug>.json`` behind otherwise, and a stale payload in a blob
store outlives the row it came from indefinitely. A subject that was renamed is
not that case any more -- the old key holds the alias the rename left behind, and
the run still produces it.

Only anonymous responses are here, because a key in a blob store is served to
whoever asks for it. That rules out four endpoints on purpose:

* ``me/documents/`` and ``subscriptions/`` are per-caller by definition.
* ``surveys/`` and ``surveys/<pk>/results/`` are staff-only.
* ``sets/<uuid>/`` reads without a credential, but only because holding the
  unguessable id *is* the permission. That model does not survive a store whose
  keys can be listed, and a set is edited by its owner between runs, so a
  precomputed copy would be both a leak and stale on the page that shows it.

``ratings/<doc>/`` is included, as its anonymous body: the public average and
count, with ``your_rating`` null. The endpoint varies by caller only in that
one field, and DRF already marks the live response ``Vary: Authorization``;
what is stored here is the anonymous half, which is what an unauthenticated
reader would have got.
"""

import json
import re
from collections import defaultdict

from popularity.api import PopularityList
from ratings.api import RatingDetail
from ratings.models import Rating
from stats.api import DocumentStatsList
from subjects.api import SubjectDetail
from subjects.models import Subject, SubjectAlias
from subjects.tree import rollup
from surveys.api import OpenSurveyList, SurveyDefinition
from surveys.models import Survey

from .render import render_anonymous

TASKS = {}


def _reserialize(payload):
    """Back to bytes the way DRF's JSONRenderer would have written them.

    Matched deliberately: every task that adds nothing writes the view's own bytes
    untouched, and the ones that do add something must differ only by the keys they
    added. The test strips the additions and compares byte for byte, which holds only
    if the separators and escaping agree with the renderer's.
    """
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def _augment(body, add):
    """Parse a rendered payload, let `add` put keys into it, and re-serialise.

    Only ever adds keys. Retyping an existing one is the change that breaks a caller,
    and it is what Reef asks Red not to do to it, so the precomputer holds itself to
    the same rule.
    """
    payload = json.loads(body)
    add(payload)
    return _reserialize(payload)


# What a precomputed row carries. rfcmeta's reduction holds more than this, for
# change detection, and projecting here rather than passing it through keeps a field
# added there from silently appearing in what Reef publishes.
PUBLISHED_FIELDS = ("title", "subseries")


def _subject_index(index):
    """The whole vocabulary in one payload: the tree, the assignments, the titles.

    Not the list endpoint's bytes, and the only task here that is not. Red fetches
    this per route and renders the page from it, so it has to carry the titles, and
    Reef has no titles to serve from the endpoint: it holds no document metadata and
    resolves it here, from Red's own index, at precompute time.

    Two maps rather than two lists, both keyed, so that a caller looks a subject or
    a document up directly instead of building an index of its own or scanning. And
    the metadata sits in one map referenced by identifier rather than beside each
    subject that carries the document, which would repeat every title once per
    covering subject -- about three times over at this vocabulary's depth.

    What is not here is the subtree of each subject. It is derivable from `path` and
    `children` in the pass a caller is already making, and writing it out would
    store every identifier once per ancestor.
    """
    direct, covered = rollup()
    rows = list(Subject.objects.order_by("path"))
    children = defaultdict(list)
    for subject in rows:
        ancestors = subject.ancestor_slugs
        if ancestors:
            children[ancestors[-1]].append(subject.slug)

    subjects_by_slug = {}
    for subject in rows:
        subjects_by_slug[subject.slug] = {
            "id": subject.pk,
            "name": subject.name,
            "description": subject.description,
            "parent": subject.ancestor_slugs[-1] if subject.ancestor_slugs else None,
            "path": subject.path,
            "children": children.get(subject.slug, []),
            "documents": direct.get(subject.path, []),
            "document_count": len(direct.get(subject.path, [])),
            "document_count_deep": len(covered.get(subject.path, [])),
        }

    # Every identifier the file mentions, and only those: the union of the direct
    # assignments is exactly what the subject entries reference, because the subtree
    # lists are not written out.
    mentioned = sorted(
        {doc for subject in subjects_by_slug.values() for doc in subject["documents"]}
    )
    return {
        "documents": {doc: _meta(index, doc) for doc in mentioned},
        "subjects": subjects_by_slug,
    }


def _meta(index, doc_id):
    """The metadata block a row carries, null when it cannot be resolved.

    Null rather than omitted or echoed back as the identifier, so a reader can tell
    "no such document" from "not looked up".
    """
    resolved = index.get(doc_id) if index is not None else None
    if resolved is None:
        return {"title": None, "subseries": []}
    return {field: resolved[field] for field in PUBLISHED_FIELDS}


def task(name, *, owns, per_document=False):
    """Register a task under name.

    owns is a regex matching every key the task is responsible for, used by the
    purge. per_document marks a task whose output is keyed by document
    identifier, which is what --doc narrows.
    """

    def register(func):
        func.task_name = name
        func.owns = re.compile(owns)
        func.per_document = per_document
        TASKS[name] = func
        return func

    return register


@task("stats", owns=r"^stats\.json$")
def stats(docs=None, index=None):
    """Rating, subscriber and set counts for the whole series, in one file.

    The expensive one, and the reason this command exists: the endpoint is
    unpaginated by design because Red wants the series in a single call, so it
    aggregates every rating, subscription and set entry on each request.
    """
    body = render_anonymous(DocumentStatsList.as_view(), "/api/reef/stats/")

    def add(rows):
        for row in rows:
            row.update(_meta(index, row["doc"]))

    yield "stats.json", _augment(body, add)


@task("popularity", owns=r"^popularity\.json$")
def popularity(docs=None, index=None):
    """The curated most-popular list."""
    body = render_anonymous(PopularityList.as_view(), "/api/reef/popularity/")

    def add(rows):
        for row in rows:
            row.update(_meta(index, row["rfc"]))

    yield "popularity.json", _augment(body, add)


@task("subjects", owns=r"^subjects\.json$|^subjects/[^/]+\.json$")
def subjects(docs=None, index=None):
    """The vocabulary as one index file, and each subject with its own documents."""
    yield "subjects.json", _reserialize(_subject_index(index))
    detail = SubjectDetail.as_view()
    # all_objects, so a retired subject still gets a file. subjects.json above does
    # not list it -- it is not offered any more -- but Red has links naming it, and
    # the file is what redirects them. What the view returns for one is only slug,
    # retired and merged_into.
    for slug in Subject.all_objects.values_list("slug", flat=True).order_by("slug"):
        body = render_anonymous(detail, f"/api/reef/subjects/{slug}/", slug=slug)

        def add(payload):
            # A sibling map rather than turning `documents` into a list of objects:
            # retyping an existing key is the change that breaks a caller, and a map
            # also grows a field later without retyping anything. A retired subject
            # has no documents key at all, so this adds nothing to its redirect stub.
            if "documents" not in payload:
                # A retired subject's payload is a redirect and nothing else, so
                # there is nothing to describe. Keyed on the array's presence rather
                # than on its length, so a live subject with no documents still
                # carries an empty map beside its empty array.
                return
            payload["document_meta"] = {
                doc: _meta(index, doc) for doc in payload["documents"]
            }

        yield f"subjects/{slug}.json", _augment(body, add)

    # Aliases sit in the same directory because they answer the same read: a name
    # arrives from a link without the caller knowing which kind it is. Shadowed ones
    # are skipped rather than overwriting the subject's own file, which is what the
    # detail read does with them too -- a subject's slug wins the lookup, so the alias
    # would never be served anyway.
    shadowed = Subject.all_objects.values("slug")
    aliases = SubjectAlias.objects.exclude(slug__in=shadowed).order_by("slug")
    for slug in aliases.values_list("slug", flat=True):
        yield (
            f"subjects/{slug}.json",
            render_anonymous(detail, f"/api/reef/subjects/{slug}/", slug=slug),
        )


@task("surveys", owns=r"^surveys/open\.json$|^surveys/[^/]+/definition\.json$")
def surveys(docs=None, index=None):
    """Open surveys, and the definition of each one a visitor may run.

    Only OPEN surveys get a definition file. An authenticated-visibility survey
    refuses an anonymous caller, so there is no anonymous body to store, and
    the runner has to ask the API for it with the visitor's credential.
    """
    yield (
        "surveys/open.json",
        render_anonymous(OpenSurveyList.as_view(), "/api/reef/surveys/open/"),
    )
    definition = SurveyDefinition.as_view()
    open_surveys = Survey.objects.filter(
        status=Survey.Status.PUBLISHED, visibility=Survey.Visibility.OPEN
    ).values_list("slug", flat=True)
    for slug in open_surveys.order_by("slug"):
        yield (
            f"surveys/{slug}/definition.json",
            render_anonymous(
                definition, f"/api/reef/surveys/{slug}/definition/", slug=slug
            ),
        )


@task("ratings", owns=r"^ratings/[^/]+\.json$", per_document=True)
def ratings(docs=None, index=None):
    """One file per rated document: the public average and count.

    Documents nobody has rated are left out rather than stored as zeros. The
    endpoint answers for any identifier, so a reader asking about an unrated
    document gets its empty aggregate from the API; writing a file for every
    document in the series to say "no ratings" would be most of the series.
    """
    rated = Rating.objects.values_list("rfc", flat=True).distinct().order_by("rfc")
    if docs is not None:
        rated = rated.filter(rfc__in=docs)
    detail = RatingDetail.as_view()
    for doc in rated:
        body = render_anonymous(detail, f"/api/reef/ratings/{doc}/", rfc=doc)

        def add(payload, doc=doc):
            payload.update(_meta(index, doc))

        yield f"ratings/{doc}.json", _augment(body, add)
