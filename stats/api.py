# Copyright The IETF Trust 2026, All Rights Reserved
"""Per-document engagement numbers, for Red's build-time precompute."""

import uuid
from collections import defaultdict

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Avg, Count
from django.db.models.fields.json import KeyTextTransform
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from docsets.models import DocumentSet, DocumentSetEntry
from ratings.models import Rating
from reef.docids import normalize_doc_id
from subscriptions.models import Subscription

from .serializers import DocumentStatsSerializer


def _rating_stats():
    return {
        row["rfc"]: (row["average"], row["count"])
        for row in Rating.objects.values("rfc").annotate(
            average=Avg("value"), count=Count("id")
        )
    }


def _set_counts():
    """Sets holding each document, private ones included, deleted ones not.

    The numbers are aggregate and name nobody: a count of one says that
    somebody tracks this document, not who. Private sets are counted too, so
    that the number does not move when staff unpublish one and thereby say
    something about that set.

    A set staff have taken down is excluded, because it is meant to read as one
    that never existed and this is the last place it would otherwise show. The
    counts do move by one when that happens, but a takedown removes the set
    from every other read path too, so the number is not what gives it away.
    """
    return dict(
        DocumentSetEntry.objects.filter(document_set__deleted_at__isnull=True)
        .values("doc")
        .annotate(sets=Count("document_set", distinct=True))
        .values_list("doc", "sets")
    )


def _subscriber_counts():
    """Distinct users subscribed to each document.

    Counts the two kinds that name a document: rfc, which holds the identifier
    in params, and set, which reaches it through the set's membership. The
    predicate kinds are excluded, because new_rfc would otherwise add every one
    of its subscribers to every recent RFC and flatten the number into noise.

    Merged in Python rather than SQL because a user holding both an rfc and a
    set subscription for one document is one subscriber, and the two paths are
    different joins. The inputs are subscriptions and set entries, not the
    document series, so this stays small.
    """
    users_by_doc = defaultdict(set)

    rfc_pairs = (
        Subscription.objects.filter(kind=Subscription.Kind.RFC)
        .annotate(doc=KeyTextTransform("rfc", "params"))
        .values_list("doc", "user_id")
    )
    # A subscription to a set staff have taken down counts for nothing, the
    # same as a subscription to a set that was really deleted, which would have
    # gone with it.
    set_pairs = Subscription.objects.filter(
        kind=Subscription.Kind.SET,
        document_set__entries__isnull=False,
        document_set__deleted_at__isnull=True,
    ).values_list("document_set__entries__doc", "user_id")

    for doc, user_id in [*rfc_pairs, *set_pairs]:
        users_by_doc[doc].add(user_id)
    return {doc: len(users) for doc, users in users_by_doc.items()}


class DocumentStatsList(APIView):
    """Rating, subscriber and set numbers per document.

    Public and unpaginated: Red precomputes the whole series in one call at
    build time, which is too many identifiers to name in a query string.
    Filtering with doc is for one-off lookups.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "doc",
                str,
                many=True,
                description=(
                    "Document identifier, repeatable. Omit to get every "
                    "document that has any engagement at all. A named "
                    "document is always returned, with zeros if it has none."
                ),
            ),
            OpenApiParameter(
                "set",
                OpenApiTypes.UUID,
                description=(
                    "Document set id. Returns a row per document the set "
                    "holds, including members with no engagement. Public sets "
                    "resolve for anyone; a private set resolves only for its "
                    "owner, and 404s otherwise. Combines with doc as an "
                    "intersection."
                ),
            ),
        ],
        responses=DocumentStatsSerializer(many=True),
    )
    def get(self, request):
        ratings = _rating_stats()
        subscribers = _subscriber_counts()
        sets = _set_counts()

        docs = None
        if requested := request.query_params.getlist("doc"):
            try:
                docs = {normalize_doc_id(doc) for doc in requested}
            except DjangoValidationError as exc:
                raise ValidationError({"doc": exc.messages}) from exc

        if (set_id := request.query_params.get("set")) is not None:
            members = self._documents_in_set(request, set_id)
            docs = members if docs is None else docs & members

        if docs is None:
            docs = set(ratings) | set(subscribers) | set(sets)

        rows = [
            {
                "doc": doc,
                "rating_average": ratings.get(doc, (None, 0))[0],
                "rating_count": ratings.get(doc, (None, 0))[1],
                "subscriber_count": subscribers.get(doc, 0),
                "set_count": sets.get(doc, 0),
            }
            for doc in sorted(docs)
        ]
        return Response(DocumentStatsSerializer(rows, many=True).data)

    @staticmethod
    def _documents_in_set(request, raw_id):
        """The documents a set holds, if the caller may know what they are.

        This endpoint is anonymous, so an unguarded set filter would let anyone
        read an unpublished set's membership off the rows it returned.
        Aggregate counts naming nobody is one thing; listing what a named
        person is tracking is another.

        A private set 404s rather than 403s, matching the public set read, so
        the filter does not confirm that one exists.
        """
        try:
            set_id = uuid.UUID(raw_id)
        except (AttributeError, TypeError, ValueError):
            raise ValidationError({"set": "Must be a document set id."}) from None

        document_set = (
            DocumentSet.objects.readable_by(request.user).filter(pk=set_id).first()
        )
        if document_set is None:
            raise NotFound("No such document set.")
        return set(document_set.entries.values_list("doc", flat=True))
