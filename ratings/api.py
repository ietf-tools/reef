# Copyright The IETF Trust 2026, All Rights Reserved
from django.db.models import Avg, Count
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Rating
from .serializers import RatingAggregateSerializer, RatingWriteSerializer


def _aggregate(rfc):
    agg = Rating.objects.filter(rfc=rfc).aggregate(
        average=Avg("value"), count=Count("id")
    )
    return {"rfc": rfc, "average": agg["average"], "count": agg["count"]}


class RatingDetail(APIView):
    """GET the public aggregate (anonymous); PUT the caller's rating (bearer)."""

    def get_permissions(self):
        if self.request.method == "PUT":
            return [IsAuthenticated()]
        return [AllowAny()]

    @extend_schema(responses=RatingAggregateSerializer)
    def get(self, request, rfc):
        return Response(RatingAggregateSerializer(_aggregate(rfc)).data)

    @extend_schema(request=RatingWriteSerializer, responses=RatingAggregateSerializer)
    def put(self, request, rfc):
        serializer = RatingWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        Rating.objects.update_or_create(
            rfc=rfc,
            user=request.user,
            defaults={"value": serializer.validated_data["value"]},
        )
        return Response(RatingAggregateSerializer(_aggregate(rfc)).data)
