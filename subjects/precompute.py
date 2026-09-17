# Copyright The IETF Trust 2026, All Rights Reserved
"""The subject reads as the precomputer publishes them, and nothing else.

Everything here is a serializer and a view, and none of it is routed. The
precomputer calls views directly -- ``render_anonymous`` builds a request with
``RequestFactory`` and invokes the callable, so the path it passes is cosmetic --
so a published file needs no URL. What does need one is drf-spectacular, which
generates from a urlconf, and that is ``reef.urls_contract``: it exists so the
contract can describe these payloads, and no deployment serves it.

The point of doing it this way is that the file is a view's bytes, like every
other key in the store, so ``reef_api.yaml`` describes it and no separate schema
has to. A serializer cannot emit a field it does not declare, so nothing else has
to police drift between the file and the contract.

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
from .tree import (
    documents_under,
    rollup,
    subject_tree,
    tree_ancestors,
    tree_descendants,
)


def _document_metadata(mapping, doc):
    """What a document contributes to the index file, null when unresolved.

    Null rather than omitted or echoed back as the identifier, so a reader can
    tell "no such document" from "not looked up". Only what a subject listing
    needs to render a row -- the fuller set a subject's own file carries is
    ``_full_document_metadata`` below.
    """
    resolved = mapping.get(doc) if mapping is not None else None
    if resolved is None:
        return {"title": None, "subseries": []}
    return {"title": resolved["title"], "subseries": list(resolved["subseries"])}


#: Everything rfcmeta resolves about a document except abstract, which is not in
#: the reduced mapping at all -- see reef.rfcmeta._abstracts -- and is fetched
#: separately below. Adding a field to rfcmeta's reduction does not publish it
#: here on its own; this is the one place that has to learn about it too.
_FULL_DOCUMENT_METADATA_FIELDS = (
    "title",
    "subseries",
    "status",
    "status_name",
    "stream",
    "stream_name",
    "obsoletes",
    "obsoleted_by",
    "updates",
    "updated_by",
    "authors",
    "published",
    "identifiers",
    "area",
    "group",
    "keywords",
    "pages",
)

_NULL_FULL_DOCUMENT_METADATA = {
    "title": None,
    "subseries": [],
    "status": None,
    "status_name": None,
    "stream": None,
    "stream_name": None,
    "obsoletes": [],
    "obsoleted_by": [],
    "updates": [],
    "updated_by": [],
    "authors": [],
    "published": None,
    "identifiers": [],
    "area": None,
    "group": None,
    "keywords": [],
    "pages": None,
    "abstract": None,
}


def _full_document_metadata(mapping, doc):
    """What a document contributes to its own subject's file, null when unresolved.

    Only a subject's own file carries this much: the index lists every document
    a subject covers, and repeating the full set there once per subject would
    bloat a file most readers never need it from.
    """
    resolved = mapping.get(doc) if mapping is not None else None
    if resolved is None:
        return dict(_NULL_FULL_DOCUMENT_METADATA)
    return {
        **{field: resolved[field] for field in _FULL_DOCUMENT_METADATA_FIELDS},
        "abstract": rfcmeta.cached_abstract(doc),
    }


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
    """A document as the index file names it: just enough to render a row."""

    title = serializers.CharField(allow_null=True)
    subseries = serializers.ListField(child=serializers.CharField())


class DocumentAreaOrGroupSerializer(serializers.Serializer):
    """The area or group a document belongs to, narrowed to what a page names."""

    acronym = serializers.CharField()
    name = serializers.CharField()


class DocumentIdentifierSerializer(serializers.Serializer):
    """One persistent identifier -- a DOI or an ISSN -- Red records for a document."""

    type = serializers.CharField()
    value = serializers.CharField()


class FullDocumentMetadataSerializer(serializers.Serializer):
    """A document as its own subject's file names it: everything Red's index says."""

    title = serializers.CharField(allow_null=True)
    subseries = serializers.ListField(child=serializers.CharField())
    status = serializers.CharField(allow_null=True)
    status_name = serializers.CharField(allow_null=True)
    stream = serializers.CharField(allow_null=True)
    stream_name = serializers.CharField(allow_null=True)
    obsoletes = serializers.ListField(child=serializers.IntegerField())
    obsoleted_by = serializers.ListField(child=serializers.IntegerField())
    updates = serializers.ListField(child=serializers.IntegerField())
    updated_by = serializers.ListField(child=serializers.IntegerField())
    authors = serializers.ListField(child=serializers.CharField())
    published = serializers.CharField(allow_null=True)
    identifiers = DocumentIdentifierSerializer(many=True)
    area = DocumentAreaOrGroupSerializer(allow_null=True)
    group = DocumentAreaOrGroupSerializer(allow_null=True)
    keywords = serializers.ListField(child=serializers.CharField())
    pages = serializers.IntegerField(allow_null=True)
    abstract = serializers.CharField(allow_null=True)


class SubjectMetadataSerializer(serializers.Serializer):
    """A subject named by another subject's file, so a page can render it.

    The same fields ``Subject``/``SubjectIndexEntrySerializer`` already carry,
    minus what only makes sense on a subject's own file (``children``, ``path``,
    the slug is the key). A tree of these is what lets a page build a listing
    straight from ``subject_meta``, with no second fetch for the counts and
    description a heading also wants to show.
    """

    name = serializers.CharField()
    description = serializers.CharField()
    document_count = serializers.IntegerField()
    document_count_deep = serializers.IntegerField()


class SubjectIndexEntrySerializer(serializers.Serializer):
    """One subject in the index file.

    Field order is the file's key order: a run that finds the same data must
    write the same bytes. It is the list serializer's fields plus the two the
    index adds.
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


def build_index(direct=None, covered=None, tree=None):
    """The index payload, in the order it is published in.

    Subjects in tree order, so a caller rendering top to bottom gets children
    under their parents. Documents sorted, so a run that found the same data
    writes the same bytes.

    direct and covered are rollup()'s own return value and tree is
    subject_tree()'s, each accepted rather than always recomputed so that a
    caller already holding one -- the precompute run also snapping every
    subject's own file's ancestors and descendants off the same tree -- does
    not pay for it twice.
    """
    if direct is None or covered is None:
        direct, covered = rollup()
    if tree is None:
        tree = subject_tree(direct, covered)

    # Every identifier the file mentions and only those: the union of the direct
    # assignments is exactly what the entries reference, because the subtrees are
    # not written out.
    mentioned = sorted({doc for entry in tree.values() for doc in entry["documents"]})
    mapping = _mapping()
    return {
        "documents": {doc: _document_metadata(mapping, doc) for doc in mentioned},
        "subjects": tree,
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
            "repeated beside each subject that covers the document. A document's "
            "fuller metadata -- status, authors, abstract and the rest -- is on its "
            "own subject's file, not repeated here for every subject that covers "
            "it.\n\n"
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
        precomputed = getattr(request, "precomputed_context", None) or {}
        index = build_index(
            precomputed.get("direct"),
            precomputed.get("covered"),
            precomputed.get("subject_tree"),
        )
        return Response(SubjectIndexSerializer(index).data)


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

    @extend_schema_field(serializers.DictField(child=FullDocumentMetadataSerializer()))
    def get_document_meta(self, obj):
        mapping = self.context.get("rfc_index", _mapping())
        return {
            assignment.doc: _full_document_metadata(mapping, assignment.doc)
            for assignment in obj.assignments.all()
        }

    @extend_schema_field(serializers.DictField(child=SubjectMetadataSerializer()))
    def get_subject_meta(self, obj):
        """Every subject this file names, and no others, in full enough to draw a row.

        Every ancestor up to the root, and every descendant through the whole
        branch beneath it -- not siblings, and not the subject itself, which
        carries these same fields at the top level already.

        tree, when in context, is subject_tree()'s slug-keyed structure: the
        precompute run builds it once for subjects.json and hands the same
        object to every subject's own file, so this walks parent/children
        pointers already in memory instead of a query per ancestor and a query
        for the whole subtree. A caller with no tree in context -- outside a
        precompute run -- gets the same answer from queries instead.
        """
        tree = self.context.get("subject_tree")
        if tree is not None:
            return {
                slug: {
                    "name": tree[slug]["name"],
                    "description": tree[slug]["description"],
                    "document_count": tree[slug]["document_count"],
                    "document_count_deep": tree[slug]["document_count_deep"],
                }
                for slug in tree_ancestors(tree, obj.slug)
                + tree_descendants(tree, obj.slug)
            }

        direct_counts = self.context.get("direct_counts", {})
        covered_counts = self.context.get("covered_counts", {})

        def describe(row):
            return {
                "name": row.name,
                "description": row.description,
                "document_count": direct_counts.get(row.path, row.assignments.count()),
                "document_count_deep": covered_counts.get(
                    row.path, len(documents_under(row))
                ),
            }

        ancestors = ancestor_paths(obj.path)
        named = {}
        if ancestors:
            rows = Subject.all_objects.filter(path__in=ancestors).order_by("path")
            named.update({row.slug: describe(row) for row in rows})
        named.update({row.slug: describe(row) for row in Subject.objects.under(obj)})
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
            "plus `document_meta`, Red's own metadata for each document assigned "
            "here, and `subject_meta`, every ancestor up to the root and every "
            "descendant through this subject's whole branch -- not siblings -- so "
            "that a page can draw a breadcrumb or a nested listing without a "
            "second fetch for the rest of the vocabulary.\n\n"
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

    def get_serializer_context(self):
        """Folds in what render_anonymous's caller precomputed for the whole run.

        Absent when nothing was passed -- a retired subject or an alias never
        reaches get_subject_meta, and a caller outside the precompute run has
        nothing to hand over -- in which case get_subject_meta and
        get_document_count/get_document_count_deep fall back to queries
        instead, same as an uncached SubjectSerializer would.
        """
        context = super().get_serializer_context()
        extra = getattr(self.request, "precomputed_context", None)
        if extra:
            context.update(
                {
                    k: v
                    for k, v in extra.items()
                    if k in ("direct_counts", "covered_counts", "subject_tree")
                }
            )
        return context
