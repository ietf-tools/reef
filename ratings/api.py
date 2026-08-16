# Copyright The IETF Trust 2026, All Rights Reserved
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Avg, Count
from django.utils.cache import patch_vary_headers
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from reef.docids import normalize_doc_id

from .models import Rating
from .serializers import RatingAggregateSerializer, RatingWriteSerializer


def _canonical(rfc):
    """Canonicalize the identifier in the path, or 400.

    The path segment is what Red sends, and /ratings/9110/, /ratings/rfc9110/
    and /ratings/RFC%209110/ all mean the same document, so they have to reach
    the same rows.
    """
    try:
        return normalize_doc_id(rfc, default_series="rfc")
    except DjangoValidationError as exc:
        raise ValidationError({"rfc": exc.messages}) from exc


def _aggregate(rfc, user=None):
    agg = Rating.objects.filter(rfc=rfc).aggregate(
        average=Avg("value"), count=Count("id")
    )
    own = None
    if user is not None and user.is_authenticated:
        own = (
            Rating.objects.filter(rfc=rfc, user=user)
            .values_list("value", flat=True)
            .first()
        )
    return {
        "rfc": rfc,
        "average": agg["average"],
        "count": agg["count"],
        "your_rating": own,
    }


class RatingDetail(APIView):
    """One RFC's rating: the public aggregate, plus the caller's own.

    The two methods differ in permission and in audience, so each carries its
    own ``extend_schema`` wording rather than sharing this docstring, which
    drf-spectacular would otherwise publish as the description of both.
    """

    def get_permissions(self):
        if self.request.method in ("PUT", "DELETE"):
            return [IsAuthenticated()]
        return [AllowAny()]

    @extend_schema(
        summary="Read an RFC's rating",
        description=(
            "Return the public average and count of ratings for one RFC. Open "
            "to anonymous callers. A credential adds nothing but `your_rating`, "
            "the caller's own 1-5 rating of this RFC, which is null if they "
            "have not rated it; for an anonymous caller it is always null.\n\n"
            "The identifier is canonicalized, so `9110`, `rfc9110` and "
            "`RFC 9110` all address the same document."
        ),
        responses=RatingAggregateSerializer,
    )
    def get(self, request, rfc):
        data = _aggregate(_canonical(rfc), request.user)
        response = Response(RatingAggregateSerializer(data).data)
        # The body now depends on who is asking, so it must not be served from
        # a shared cache to the next caller. Nothing caches /api/reef/ today;
        # this is here so that adding a cache later cannot leak one user's
        # rating to another.
        patch_vary_headers(response, ("Authorization", "Cookie"))
        return response

    @extend_schema(
        summary="Set the caller's rating of an RFC",
        description=(
            "Record the authenticated caller's 1-5 rating of one RFC, "
            "replacing their previous rating of it if there is one. Requires a "
            "credential. Returns the same body as GET, so the response carries "
            "the recomputed average and count along with `your_rating` echoing "
            "the value just set."
        ),
        request=RatingWriteSerializer,
        responses=RatingAggregateSerializer,
    )
    def put(self, request, rfc):
        rfc = _canonical(rfc)
        serializer = RatingWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        Rating.objects.update_or_create(
            rfc=rfc,
            user=request.user,
            defaults={"value": serializer.validated_data["value"]},
        )
        return Response(RatingAggregateSerializer(_aggregate(rfc, request.user)).data)

    @extend_schema(
        summary="Withdraw the caller's rating of an RFC",
        description=(
            "Remove the authenticated caller's rating of one RFC, so it no "
            "longer counts towards the average. Requires a credential. "
            "Idempotent: a caller who has not rated this RFC gets the same "
            "response as one whose rating was just removed. Returns the same "
            "body as GET, so the response carries the recomputed average and "
            "count, with `your_rating` now null."
        ),
        request=None,
        # Keyed by status: for DELETE, drf-spectacular otherwise assumes the
        # usual bodyless 204 and drops the serializer from the schema.
        responses={200: RatingAggregateSerializer},
    )
    def delete(self, request, rfc):
        rfc = _canonical(rfc)
        # 200 with the recomputed aggregate rather than 204, matching PUT: the
        # client redraws the same widget after either call, and a withdrawal
        # moves the public average it is displaying.
        Rating.objects.filter(rfc=rfc, user=request.user).delete()
        return Response(RatingAggregateSerializer(_aggregate(rfc, request.user)).data)
