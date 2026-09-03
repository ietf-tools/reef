# Copyright The IETF Trust 2026, All Rights Reserved
"""The subject reads as the precomputer publishes them, and nothing else.

Everything here is a serializer and a view, and none of it is routed. The
precomputer calls views directly -- ``render_anonymous`` builds a request with
``RequestFactory`` and invokes the callable, so the path it passes is cosmetic --
so a published file needs no URL. What does need one is drf-spectacular, which
generates from a urlconf, and that is ``reef.urls_contract``: it exists so the
contract can describe these payloads, and no deployment serves it.

The point of doing it this way is that the file is a view's bytes, like every
other key in the store, so ``reef_api.yaml`` describes it and there is nothing
left for a hand-written JSON Schema to police. A serializer cannot emit a field
it does not declare, which is the one class of drift the deleted
``precomputer/schemas.py`` was built to catch.

Two things separate these shapes from the served ones, and both are document
metadata. Reef stores none: ``reef.rfcmeta`` reads it from Red's published index
and never writes a row. Resolving it here rather than on the served endpoints
keeps it off a request path a browser reaches, which is the whole reason these
views are separate classes rather than fields on the live ones.
"""

from drf_spectacular.utils import (
    PolymorphicProxySerializer,
    extend_schema,
    extend_schema_field,
    extend_schema_view,
)
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from reef import rfcmeta

from .api import SubjectDetail
from .models import Subject, ancestor_paths
from .serializers import (
    RetiredSubjectSerializer,
    SubjectAliasSerializer,
    SubjectDetailSerializer,
)
from .tree import rollup


def _document_metadata(mapping, doc):
    """What a document contributes to a published file, null when unresolved.

    Null rather than omitted or echoed back as the identifier, so a reader can
    tell "no such document" from "not looked up".
    """
    resolved = mapping.get(doc) if mapping is not None else None
    if resolved is None:
        return {"title": None, "subseries": []}
    return {"title": resolved["title"], "subseries": list(resolved["subseries"])}


def _mapping():
    """Red's index if something has already loaded it, and never a fetch.

    ``cached_mapping`` rather than ``get_index``: this runs inside a view, and a
    view that fetches nine thousand entries from another service is not a view.
    The precompute command warms the shared cache once per run before any task
    executes, so in the only caller there is it is warm. A cold one publishes
    nulls, which is what ``--no-metadata`` means and what an unresolvable
    identifier has always produced.
    """
    return rfcmeta.cached_mapping()


class DocumentMetadataSerializer(serializers.Serializer):
    """A document as a published file names it: what Red's index says about it."""

    title = serializers.CharField(allow_null=True)
    subseries = serializers.ListField(child=serializers.CharField())


class SubjectMetadataSerializer(serializers.Serializer):
    """A subject named by another subject's file, so a page can render it.

    Only the curated name. ``children`` and ``path`` carry slugs, and a page
    showing "Email" rather than ``email`` would otherwise have to read the whole
    vocabulary to find one word.
    """

    name = serializers.CharField()


class SubjectIndexEntrySerializer(serializers.Serializer):
    """One subject in the index file.

    Field order is the file's key order and is load-bearing while the byte
    equality test against the old hand-built payload stands. It is the list
    serializer's fields plus the two the index adds.
    """

    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField()
    parent = serializers.CharField(allow_null=True)
    path = serializers.CharField()
    children = serializers.ListField(child=serializers.CharField())
    documents = serializers.ListField(child=serializers.CharField())
    document_count = serializers.IntegerField()
    document_count_deep = serializers.IntegerField()


class SubjectIndexSerializer(serializers.Serializer):
    """The whole vocabulary in one payload: the tree, the assignments, the titles.

    Two maps rather than two lists, both keyed, so that a caller looks a subject
    or a document up directly instead of building an index of its own. The
    metadata sits in one map referenced by identifier rather than beside each
    subject that carries the document, which would repeat every title once per
    covering subject.

    What is deliberately absent is each subject's subtree. It is derivable from
    ``path`` and ``children`` in the pass a caller is already making, and writing
    it out would store every identifier once per ancestor.
    """

    documents = serializers.DictField(child=DocumentMetadataSerializer())
    subjects = serializers.DictField(child=SubjectIndexEntrySerializer())


def build_index():
    """The index payload, in the order it is published in.

    Subjects in tree order, so a caller rendering top to bottom gets children
    under their parents. Documents sorted, so a run that found the same data
    writes the same bytes.
    """
    direct, covered = rollup()
    rows = list(Subject.objects.order_by("path"))

    children = {}
    for subject in rows:
        ancestors = subject.ancestor_slugs
        if ancestors:
            children.setdefault(ancestors[-1], []).append(subject.slug)

    subjects = {}
    for subject in rows:
        ancestors = subject.ancestor_slugs
        subjects[subject.slug] = {
            "id": subject.pk,
            "name": subject.name,
            "description": subject.description,
            "parent": ancestors[-1] if ancestors else None,
            "path": subject.path,
            "children": children.get(subject.slug, []),
            "documents": direct.get(subject.path, []),
            "document_count": len(direct.get(subject.path, [])),
            "document_count_deep": len(covered.get(subject.path, [])),
        }

    # Every identifier the file mentions and only those: the union of the direct
    # assignments is exactly what the entries reference, because the subtrees are
    # not written out.
    mentioned = sorted(
        {doc for entry in subjects.values() for doc in entry["documents"]}
    )
    mapping = _mapping()
    return {
        "documents": {doc: _document_metadata(mapping, doc) for doc in mentioned},
        "subjects": subjects,
    }


@extend_schema_view(
    get=extend_schema(
        # Named, because the two precomputed paths share a prefix and drf-spectacular
        # would otherwise derive the same id for both and disambiguate with a numeral
        # suffix whose order nothing pins.
        operation_id="precomputed_subject_index_retrieve",
        summary="The published subject index",
        description=(
            "Not a served endpoint. This describes the payload the precomputer "
            "publishes to `subjects.json` in the blob store, which is where Red "
            "reads it from; no deployment routes this path.\n\n"
            "It is the vocabulary as a tree with every assignment and every document "
            "title, in one file, so that a caller renders the subject listing from a "
            "single fetch. Two keyed maps: `subjects` by slug in tree order, and "
            "`documents` by identifier, referenced from the entries rather than "
            "repeated beside each subject that covers the document.\n\n"
            "Retired subjects and aliases are absent: they are not offered, and the "
            "per-subject files are what answer for them."
        ),
        responses={200: SubjectIndexSerializer},
    ),
)
class SubjectIndex(APIView):
    """The index file, rendered by the precomputer and by nothing else."""

    permission_classes = [AllowAny]

    def get(self, request):
        return Response(SubjectIndexSerializer(build_index()).data)


class PrecomputedSubjectDetailSerializer(SubjectDetailSerializer):
    """A subject's own file: the served shape plus what a page cannot look up.

    Both additions are sibling maps rather than changes to the arrays they
    describe. Retyping `documents` into a list of objects, or `children` into
    one, is the change that breaks a caller, and it is what Reef asks Red not to
    do to it. A map also grows a field later without retyping anything.
    """

    document_meta = serializers.SerializerMethodField()
    subject_meta = serializers.SerializerMethodField()

    class Meta(SubjectDetailSerializer.Meta):
        fields = [
            *SubjectDetailSerializer.Meta.fields,
            "document_meta",
            "subject_meta",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.DictField(child=DocumentMetadataSerializer()))
    def get_document_meta(self, obj):
        mapping = self.context.get("rfc_index", _mapping())
        return {
            assignment.doc: _document_metadata(mapping, assignment.doc)
            for assignment in obj.assignments.all()
        }

    @extend_schema_field(serializers.DictField(child=SubjectMetadataSerializer()))
    def get_subject_meta(self, obj):
        """The curated names of the subjects this file names, and no others.

        Its ancestors, from the prefixes of `path`, and its live children. Not
        the subject itself, which carries its own `name`, and not the whole
        vocabulary, which is the index file's business.

        One query for the ancestors on exact paths, and the children rows are
        already loaded to produce `children`.
        """
        ancestors = ancestor_paths(obj.path)
        named = {}
        if ancestors:
            rows = Subject.all_objects.filter(path__in=ancestors).order_by("path")
            named.update({row.slug: {"name": row.name} for row in rows})
        named.update(
            {
                child.slug: {"name": child.name}
                for child in obj.live_children.order_by("path")
            }
        )
        return named


@extend_schema_view(
    get=extend_schema(
        operation_id="precomputed_subject_detail_retrieve",
        summary="A published subject file",
        description=(
            "Not a served endpoint. This describes the payload the precomputer "
            "publishes to `subjects/<slug>.json` in the blob store; no deployment "
            "routes this path.\n\n"
            "One file per subject, which is what lets a subject page in Red be a "
            "single fetch. It is the served `/api/reef/subjects/{slug}/` response "
            "plus `document_meta`, the title of each document assigned here, and "
            "`subject_meta`, the curated names of this subject's ancestors and "
            "children so that a breadcrumb need not read the whole vocabulary.\n\n"
            "A retired subject and an alias are published here too, as the same "
            "redirect stubs the served read returns, because a blob store cannot "
            "answer with a 301. Neither carries `documents`, so neither gains the "
            "two maps."
        ),
        responses={
            200: PolymorphicProxySerializer(
                component_name="PrecomputedSubjectDetailOrRedirect",
                serializers=[
                    PrecomputedSubjectDetailSerializer,
                    RetiredSubjectSerializer,
                    SubjectAliasSerializer,
                ],
                resource_type_field_name=None,
            )
        },
    ),
)
class PrecomputedSubjectDetail(SubjectDetail):
    """The served detail read, with the metadata Reef resolves rather than stores."""

    def get_serializer_class(self):
        served = super().get_serializer_class()
        if served is SubjectDetailSerializer:
            return PrecomputedSubjectDetailSerializer
        return served
