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
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import (
    OpenApiParameter,
    PolymorphicProxySerializer,
    extend_schema,
)
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny

from reef.docids import normalize_doc_id

from .models import Subject, SubjectAlias
from .serializers import (
    RetiredSubjectSerializer,
    SubjectAliasSerializer,
    SubjectDetailSerializer,
    SubjectSerializer,
)


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
        "Three shapes, told apart by which key is present. A live subject comes back "
        "in full, with `documents` and its other names in `aliases`. A retired one "
        "comes back as `slug`, `retired` and `merged_into` only: it is no longer "
        "offered and should not be rendered as current, and what is left is enough to "
        "redirect a link that names it. An alias comes back as `slug` and `alias_of`, "
        "naming the subject to redirect to.\n\n"
        "A subject's own slug always wins, so a name is never both."
    ),
    responses={
        200: PolymorphicProxySerializer(
            component_name="SubjectDetailOrRedirect",
            serializers=[
                SubjectDetailSerializer,
                RetiredSubjectSerializer,
                SubjectAliasSerializer,
            ],
            # No discriminator field: a caller branches on which keys are present,
            # and `retired` is a boolean, which cannot be an OpenAPI discriminator,
            # so this is a plain oneOf.
            resource_type_field_name=None,
        )
    },
)
class SubjectDetail(RetrieveAPIView):
    """One subject with its document list, or, if the slug is not a live subject's,
    where the name goes instead.

    This is the URL a reader arrives at from a link, so it has to answer every name
    Reef has ever published rather than only the ones still in the vocabulary. A
    retired subject resolves here and nowhere else: it is gone from the vocabulary, so
    nothing offers it any more, but Red has links naming it and a reader following one
    has to find out what it became. An alias resolves here too, for names that were
    never subjects at all.

    Both redirects come back as stubs -- enough to send the reader on, deliberately
    not enough to render as though the name were current -- which is also what makes
    them precomputable: the store this read is published into serves bodies, not 301s.
    """

    queryset = Subject.all_objects.prefetch_related("assignments", "aliases")
    permission_classes = [AllowAny]
    lookup_field = "slug"
    # Set by get_object. None while the schema generator is introspecting the view
    # without ever making a request.
    _object = None

    def get_serializer_class(self):
        if isinstance(self._object, SubjectAlias):
            return SubjectAliasSerializer
        if self._object is not None and self._object.is_retired:
            return RetiredSubjectSerializer
        return SubjectDetailSerializer

    def get_object(self):
        slug = self.kwargs[self.lookup_field]
        obj = self.filter_queryset(self.get_queryset()).filter(slug=slug).first()
        if obj is None:
            # Second, and only second. A subject's own slug wins the lookup, which is
            # what makes an alias that shadows one merely unreachable instead of
            # ambiguous, and is why nothing has to keep the two name spaces disjoint
            # for correctness.
            obj = get_object_or_404(
                SubjectAlias.objects.select_related("subject"), slug=slug
            )
        self.check_object_permissions(self.request, obj)
        self._object = obj
        return obj
