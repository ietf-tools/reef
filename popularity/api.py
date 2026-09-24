# Copyright The IETF Trust 2026, All Rights Reserved
from django.db.models import Max
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DocumentPopularity
from .serializers import PopularitySerializer


class PopularityList(APIView):
    """Every ranked RFC, most popular first. Public; unpaginated, since a consumer
    wants the whole ranking in one read and only ranked documents have a row."""

    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="popularity_list",
        summary="The popularity ranking",
        description=(
            "Every RFC with a popularity score, most popular first. `popularity` "
            "is a rank percentile in [0, 1]: 1 is the most popular ranked document "
            "and 0 the least, and only ordering is expressed, never traffic. An RFC "
            "without an entry has not been ranked. `computed_at` is when the "
            "ranking was last recomputed, null while it is empty."
        ),
        responses={200: PopularitySerializer},
    )
    def get(self, request):
        entries = DocumentPopularity.objects.all()
        computed_at = entries.aggregate(latest=Max("updated_at"))["latest"]
        return Response(
            PopularitySerializer({"computed_at": computed_at, "entries": entries}).data
        )
