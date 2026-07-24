# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny

from .models import PopularEntry
from .serializers import PopularEntrySerializer


class PopularityList(ListAPIView):
    """The curated most-popular list. Public; consumed by Red at build time."""

    queryset = PopularEntry.objects.all()
    serializer_class = PopularEntrySerializer
    permission_classes = [AllowAny]
    pagination_class = None
