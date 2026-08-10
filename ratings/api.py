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
    """GET the aggregate plus the caller's own rating; PUT the caller's rating.

    GET stays open to anonymous callers, and a credential only adds
    ``your_rating`` to the response.
    """

    def get_permissions(self):
        if self.request.method == "PUT":
            return [IsAuthenticated()]
        return [AllowAny()]

    @extend_schema(responses=RatingAggregateSerializer)
    def get(self, request, rfc):
        data = _aggregate(_canonical(rfc), request.user)
        response = Response(RatingAggregateSerializer(data).data)
        # The body now depends on who is asking, so it must not be served from
        # a shared cache to the next caller. Nothing caches /api/reef/ today;
        # this is here so that adding a cache later cannot leak one user's
        # rating to another.
        patch_vary_headers(response, ("Authorization", "Cookie"))
        return response

    @extend_schema(request=RatingWriteSerializer, responses=RatingAggregateSerializer)
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
