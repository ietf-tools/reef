# Copyright The IETF Trust 2026, All Rights Reserved
"""What a batch of documents is to the caller, in one request.

Red draws the same three controls beside every document it lists — the reader's
own rating, whether they subscribe to it, which of their sets hold it — on
pages that list fifty of them. Answering that from the per-document and
whole-list endpoints costs one request per control per document, so this is the
one call that answers all of it for a whole page.

Per-caller only. The public numbers a document also carries (average rating,
subscriber and set totals) are the same for every visitor, so they reach Red
through the data it already loads for the route, and /api/reef/stats/ still
serves the build-time sweep that produces them. Nothing here aggregates across
users, which is what keeps every query below scoped to request.user and cheap.
"""

from collections import defaultdict

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models.fields.json import KeyTextTransform
from django.utils.cache import patch_vary_headers
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from docsets.models import DocumentSet, DocumentSetEntry
from ratings.models import Rating
from reef.docids import normalize_doc_id
from subscriptions.models import Subscription

from .serializers import MyDocumentsSerializer

# How many documents one request may name. Red's longest page is a search
# result page, which tops out at fifty, so this leaves room without letting a
# single request turn into an unbounded scan of the caller's history.
MAX_BATCH_DOCS = 100


def _your_ratings(user, docs):
    """The caller's own rating per document. Covered by Rating's
    unique_together on (rfc, user), so this is an index lookup."""
    return dict(
        Rating.objects.filter(user=user, rfc__in=docs).values_list("rfc", "value")
    )


def _your_subscription_ids(user, docs):
    """The caller's `rfc`-kind subscription id per document.

    Only that kind. A reader can also reach a document through a subscription
    to a set that holds it, or to a subject it carries, but neither is the same
    thing as subscribing to the document: unticking the box would have to
    delete that whole subscription, which is not what the box says. So the box
    reflects the direct subscription and nothing else, and Red's subscribe
    control stays a thing it can undo.

    At most one row per document: the unique constraint on
    (user, kind, params, document_set, subject) makes a second identical
    subscription impossible.
    """
    rows = (
        Subscription.objects.filter(user=user, kind=Subscription.Kind.RFC)
        .annotate(doc=KeyTextTransform("rfc", "params"))
        .filter(doc__in=docs)
        .values_list("doc", "id")
    )
    return dict(rows)


def _your_set_ids(user, docs):
    """Which of the caller's own sets hold each document.

    The deleted_at filter is not redundant with owner: a related-field join
    goes through the base manager, so DocumentSet.objects excluding
    soft-deleted sets does not reach across this one. Without it a set staff
    have taken down would still tick its box for its owner, which is the one
    place a takedown is meant to be indistinguishable from never having
    existed.
    """
    rows = DocumentSetEntry.objects.filter(
        document_set__owner=user,
        document_set__deleted_at__isnull=True,
        doc__in=docs,
    ).values_list("doc", "document_set_id")

    set_ids = defaultdict(list)
    for doc, set_id in rows:
        set_ids[doc].append(set_id)
    return set_ids


class MyDocumentsList(APIView):
    """The caller's own rating, subscription and set membership per document.

    Authenticated only: every field is the caller's own, so there is nothing
    here for an anonymous caller to be told.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Read your own state for a batch of documents",
        description=(
            "Return the authenticated caller's rating, subscription and set "
            "membership for each named document, along with the caller's own "
            "sets. One call for a whole page of documents.\n\n"
            "`doc` is repeatable and identifiers are canonicalized, so "
            "`rfc9110` and `RFC 9110` address the same document; the series "
            "has to be named, so `9110` on its own is rejected. A named "
            "document is always returned, with nulls if the caller has no "
            "state for it. At most "
            f"{MAX_BATCH_DOCS} documents per request.\n\n"
            "Naming no document returns the caller's sets and an empty "
            "`documents` list, which is how a client loads the set list on its "
            "own. Unlike `/stats/`, an empty `doc` does not mean every "
            "document: that endpoint is swept once at build time, whereas this "
            "one is called on every page.\n\n"
            "Carries no public numbers. Ratings averages, subscriber counts "
            "and set counts are the same for every visitor and are served by "
            "`/stats/`."
        ),
        parameters=[
            OpenApiParameter(
                "doc",
                str,
                many=True,
                description=(
                    "Document identifier, repeatable, at most "
                    f"{MAX_BATCH_DOCS} per request. The series must be named: "
                    "rfc9110, bcp14, std66."
                ),
            ),
        ],
        responses=MyDocumentsSerializer,
    )
    def get(self, request):
        docs = self._requested_docs(request)
        user = request.user

        ratings = _your_ratings(user, docs)
        subscription_ids = _your_subscription_ids(user, docs)
        set_ids = _your_set_ids(user, docs)

        payload = {
            # The queryset itself, left for the nested serializer to walk:
            # DocumentSet.objects excludes the sets staff have taken down, so
            # scoping to the owner is the whole of the filtering here.
            "sets": DocumentSet.objects.filter(owner=user),
            "documents": [
                {
                    "doc": doc,
                    "your_rating": ratings.get(doc),
                    "your_subscription_id": subscription_ids.get(doc),
                    "your_set_ids": set_ids.get(doc, []),
                }
                for doc in docs
            ],
        }

        response = Response(MyDocumentsSerializer(payload).data)
        # The whole body is one caller's, so it must never be served from a
        # shared cache to the next one. Nothing caches /api/reef/ today; this
        # is here so that adding a cache later cannot leak it.
        patch_vary_headers(response, ("Authorization", "Cookie"))
        response["Cache-Control"] = "private, no-store"
        return response

    @staticmethod
    def _requested_docs(request):
        """The documents named in the query string, canonical and sorted.

        Sorted so that the same request answers in the same order however the
        identifiers were listed, and deduplicated because two spellings of one
        document are one document and would otherwise be two rows.
        """
        requested = request.query_params.getlist("doc")
        if len(requested) > MAX_BATCH_DOCS:
            raise ValidationError(
                {
                    "doc": (
                        f"Name at most {MAX_BATCH_DOCS} documents per request; "
                        f"got {len(requested)}."
                    )
                }
            )
        try:
            return sorted({normalize_doc_id(doc) for doc in requested})
        except DjangoValidationError as exc:
            raise ValidationError({"doc": exc.messages}) from exc
