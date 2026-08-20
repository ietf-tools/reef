# Copyright The IETF Trust 2026, All Rights Reserved
from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import SAFE_METHODS, AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DocumentSet, DocumentSetEntry
from .serializers import (
    DocumentSetOrderSerializer,
    DocumentSetSerializer,
    canonical_doc_id,
)


class OwnedSetMixin:
    """Scope to the caller's own sets, so another owner's set is a 404.

    A set staff have taken down is a 404 here too, for its owner as much as for
    anyone else: DocumentSet.objects does not see soft-deleted sets, so every
    endpoint that scopes this way reads, writes and deletes as though it had
    never existed.
    """

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return DocumentSet.objects.filter(owner=self.request.user)


class DocumentSetListCreate(OwnedSetMixin, generics.ListCreateAPIView):
    """List and create the caller's document sets."""

    serializer_class = DocumentSetSerializer

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class DocumentSetDetail(generics.RetrieveUpdateDestroyAPIView):
    """Read a set; retitle, redescribe or delete your own.

    One URL for a set, whoever is asking: the id is the whole of a set's
    identity, so a shared link is this link and there is no second read
    endpoint to keep in step with it. Reading needs no token, which is what
    makes the link shareable, and holding the id is the whole of the
    permission: a set is a thing its owner made to be passed around, and the
    id is unguessable so that passing it around is the only way in. Writing is
    the owner's alone, and a write to somebody else's set 404s rather than 403s
    so that the refusal says nothing about whose it is.

    A set staff have taken down 404s here too, for everyone alike: it is left
    out of the queryset rather than refused, so nothing confirms it exists.
    """

    serializer_class = DocumentSetSerializer

    def get_permissions(self):
        return (
            [AllowAny()] if self.request.method in SAFE_METHODS else [IsAuthenticated()]
        )

    def get_queryset(self):
        if self.request.method not in SAFE_METHODS:
            return DocumentSet.objects.filter(owner=self.request.user)
        return DocumentSet.objects.all()


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
