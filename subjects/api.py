# Copyright The IETF Trust 2026, All Rights Reserved
"""The subject vocabulary, read-only and public.

Curation happens in the admin, so there is no write path here. Two reads
cover what a caller needs: the whole vocabulary, to draw a picker or a filter,
and one subject with the documents carrying it.

Public, because a subject is public: it is rendered on an RFC page in Red
beside the document it describes, and a reader has to be able to see what
they would be subscribing to before they have signed in.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny

from reef.docids import normalize_doc_id

from .models import Subject
from .serializers import SubjectDetailSerializer, SubjectSerializer


@extend_schema(
    summary="List the subject vocabulary",
    description=(
        "Every subject that exists, in name order. Public and unpaginated: "
        "the vocabulary is curated by staff rather than self-served, so it "
        "stays small enough to hand over whole.\n\n"
        "`doc` narrows the list to the subjects carried by one document, "
        "which is how a caller renders the subjects on an RFC page. The "
        "identifier is canonicalized, so `rfc9110` and `RFC 9110` address the "
        "same document, and the series has to be named."
    ),
    parameters=[
        OpenApiParameter(
            "doc",
            str,
            description=(
                "Return only the subjects carried by this document. The "
                "series must be named: rfc9110, bcp14, std66."
            ),
        ),
    ],
)
class SubjectList(ListAPIView):
    """The whole vocabulary, or the subjects on one document."""

    serializer_class = SubjectSerializer
    permission_classes = [AllowAny]
    pagination_class = None

    def get_queryset(self):
        queryset = Subject.objects.all()
        doc = self.request.query_params.get("doc")
        if doc is None:
            return queryset
        try:
            doc = normalize_doc_id(doc)
        except DjangoValidationError as exc:
            raise ValidationError({"doc": exc.messages}) from exc
        # distinct() is not needed: unique_document_per_subject means one
        # document joins a subject at most once.
        return queryset.filter(assignments__doc=doc)


@extend_schema(
    summary="Read one subject and the documents carrying it",
    description=(
        "Addressed by slug rather than id, because this is the path whose "
        "URL a reader sees. Subscribing names the id instead, which the "
        "response also carries."
    ),
)
class SubjectDetail(RetrieveAPIView):
    """One subject, with its document list."""

    queryset = Subject.objects.prefetch_related("assignments")
    serializer_class = SubjectDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"
