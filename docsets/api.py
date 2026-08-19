# Copyright The IETF Trust 2026, All Rights Reserved
from django.db import transaction
from django.http import HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DocumentSet, DocumentSetEntry
from .serializers import (
    DocumentSetOrderSerializer,
    DocumentSetSerializer,
    canonical_doc_id,
)


class OwnedSetMixin:
    """Scope to the caller's own sets, so another owner's set is a 404."""

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return DocumentSet.objects.filter(owner=self.request.user)


class DocumentSetListCreate(OwnedSetMixin, generics.ListCreateAPIView):
    """List and create the caller's document sets."""

    serializer_class = DocumentSetSerializer

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class DocumentSetDetail(OwnedSetMixin, generics.RetrieveUpdateDestroyAPIView):
    """Read, retitle, redescribe, or delete one of the caller's sets.

    Not unpublish: sets made through the API are public, so visibility takes
    only that one value here. See DocumentSetSerializer.validate_visibility.
    """

    serializer_class = DocumentSetSerializer


class DocumentSetDocument(OwnedSetMixin, APIView):
    """Add or remove one document.

    PUT is idempotent, and the identifier is canonicalized first, so
    .../documents/RFC%209110/ and .../documents/rfc9110/ are the same entry.
    """

    @extend_schema(
        parameters=[OpenApiParameter("doc", str, OpenApiParameter.PATH)],
        request=None,
        responses={200: DocumentSetSerializer, 201: DocumentSetSerializer},
    )
    def put(self, request, pk, doc):
        document_set = get_object_or_404(self.get_queryset(), pk=pk)
        entry, created = DocumentSetEntry.objects.get_or_create(
            document_set=document_set,
            doc=canonical_doc_id(doc),
            defaults={"rank": self._next_rank(document_set)},
        )
        if created:
            document_set.save(update_fields=["updated_at"])
        return Response(
            DocumentSetSerializer(document_set).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(
        parameters=[OpenApiParameter("doc", str, OpenApiParameter.PATH)],
        responses={204: None},
    )
    def delete(self, request, pk, doc):
        document_set = get_object_or_404(self.get_queryset(), pk=pk)
        deleted, _ = document_set.entries.filter(doc=canonical_doc_id(doc)).delete()
        if deleted:
            document_set.save(update_fields=["updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @staticmethod
    def _next_rank(document_set):
        last = document_set.entries.order_by("-rank").first()
        return last.rank + 1 if last else 0


class DocumentSetOrder(OwnedSetMixin, APIView):
    """Rewrite the display order in one request.

    Ranks are replaced as a block rather than patched per entry, so a
    drag-and-drop is one call that cannot half-apply.
    """

    @extend_schema(
        request=DocumentSetOrderSerializer, responses={200: DocumentSetSerializer}
    )
    def put(self, request, pk):
        document_set = get_object_or_404(self.get_queryset(), pk=pk)
        serializer = DocumentSetOrderSerializer(
            data=request.data, context={"document_set": document_set}
        )
        serializer.is_valid(raise_exception=True)

        ordered = serializer.validated_data["documents"]
        entries = {entry.doc: entry for entry in document_set.entries.all()}
        for rank, doc in enumerate(ordered):
            entries[doc].rank = rank
        with transaction.atomic():
            DocumentSetEntry.objects.bulk_update(entries.values(), ["rank"])
            document_set.save(update_fields=["updated_at"])
        return Response(DocumentSetSerializer(document_set).data)


class PublicDocumentSetDetail(generics.RetrieveAPIView):
    """Read a published set, anonymously.

    A private set is a 404 rather than a 403: the endpoint does not confirm
    that one exists. A stale or wrong slug redirects to the current URL, since
    the id carries identity and the slug only has to be readable.
    """

    serializer_class = DocumentSetSerializer
    permission_classes = [AllowAny]
    queryset = DocumentSet.objects.filter(visibility=DocumentSet.Visibility.PUBLIC)

    # Named, because it would otherwise collide with the owner's retrieve and
    # be resolved to sets_retrieve_2 in everyone's generated client.
    @extend_schema(operation_id="sets_public_retrieve")
    def get(self, request, *args, **kwargs):
        document_set = self.get_object()
        if kwargs["slug"] != document_set.slug:
            return HttpResponsePermanentRedirect(
                reverse(
                    "documentset-public",
                    kwargs={"pk": document_set.pk, "slug": document_set.slug},
                )
            )
        return Response(self.get_serializer(document_set).data)
