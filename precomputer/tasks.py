# Copyright The IETF Trust 2026, All Rights Reserved
"""What gets precomputed, and what deliberately does not.

One task per public read endpoint. A task yields ``(key, body)`` pairs and
declares, with ``owns``, the keys in the store that are its own, so that a full
run can purge what it no longer produces: a subject that was renamed leaves a
stale ``subjects/<old-slug>.json`` behind otherwise, and a stale payload in a
blob store outlives the row it came from indefinitely.

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

import re

from popularity.api import PopularityList
from ratings.api import RatingDetail
from ratings.models import Rating
from stats.api import DocumentStatsList
from subjects.api import SubjectDetail, SubjectList
from subjects.models import Subject
from surveys.api import OpenSurveyList, SurveyDefinition
from surveys.models import Survey

from .render import render_anonymous

TASKS = {}


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
def stats(docs=None):
    """Rating, subscriber and set counts for the whole series, in one file.

    The expensive one, and the reason this command exists: the endpoint is
    unpaginated by design because Red wants the series in a single call, so it
    aggregates every rating, subscription and set entry on each request.
    """
    yield (
        "stats.json",
        render_anonymous(DocumentStatsList.as_view(), "/api/reef/stats/"),
    )


@task("popularity", owns=r"^popularity\.json$")
def popularity(docs=None):
    """The curated most-popular list."""
    yield (
        "popularity.json",
        render_anonymous(PopularityList.as_view(), "/api/reef/popularity/"),
    )


@task("subjects", owns=r"^subjects\.json$|^subjects/[^/]+\.json$")
def subjects(docs=None):
    """The whole vocabulary, and each subject with the documents carrying it."""
    yield (
        "subjects.json",
        render_anonymous(SubjectList.as_view(), "/api/reef/subjects/"),
    )
    detail = SubjectDetail.as_view()
    for slug in Subject.objects.values_list("slug", flat=True).order_by("slug"):
        yield (
            f"subjects/{slug}.json",
            render_anonymous(detail, f"/api/reef/subjects/{slug}/", slug=slug),
        )


@task("surveys", owns=r"^surveys/open\.json$|^surveys/[^/]+/definition\.json$")
def surveys(docs=None):
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
def ratings(docs=None):
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
        yield (
            f"ratings/{doc}.json",
            render_anonymous(detail, f"/api/reef/ratings/{doc}/", rfc=doc),
        )
