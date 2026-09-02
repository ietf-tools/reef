# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework import serializers

from .models import Subject, SubjectAlias
from .tree import documents_under


class SubjectSerializer(serializers.ModelSerializer):
    """A subject as a caller sees it, without its membership.

    The id is carried as well as the slug because subscribing names the id:
    the subscription holds a foreign key so that renaming a subject cannot
    detach its subscribers, and the id is the half of a subject's identity
    that a rename does not touch.

    parent and path are what let a caller build the tree from the flat list in one
    pass, with no second read and nothing nested. Both counts are carried because
    a picker wants a figure against every node without walking the subtree to get
    one, and because they are two integers.
    """

    # Read off the path rather than through the relation, which would be a query
    # per row on a list of the whole vocabulary. The parent's slug is the segment
    # before this subject's own.
    parent = serializers.SerializerMethodField()
    document_count = serializers.SerializerMethodField()
    document_count_deep = serializers.SerializerMethodField()

    class Meta:
        model = Subject
        fields = [
            "id",
            "slug",
            "name",
            "description",
            "parent",
            "path",
            "document_count",
            "document_count_deep",
        ]
        read_only_fields = fields

    def get_parent(self, obj) -> str | None:
        ancestors = obj.ancestor_slugs
        return ancestors[-1] if ancestors else None

    def get_document_count(self, obj) -> int:
        counts = self.context.get("direct_counts")
        if counts is not None:
            return counts.get(obj.path, 0)
        return obj.assignments.count()

    def get_document_count_deep(self, obj) -> int:
        counts = self.context.get("covered_counts")
        if counts is not None:
            return counts.get(obj.path, 0)
        return len(documents_under(obj))


class SubjectDetailSerializer(SubjectSerializer):
    """A subject and the documents carrying it.

    Separate from the list serializer for the reason me.MyDocumentSetSerializer
    is separate from docsets.DocumentSetSerializer: a picker needs every
    subject and no membership, so sending membership in the list would make
    the payload grow with the whole catalogue rather than with the vocabulary.
    """

    documents = serializers.SerializerMethodField()
    # The subjects immediately beneath this one, so that a caller holding one
    # subject can walk down without scanning the vocabulary asking whose parent it
    # is. Live only: a retired child is not offered, and this is the offer.
    children = serializers.SerializerMethodField()
    # The other names this subject answers to, so that a picker can match what a
    # reader typed without a second read. Names only: an alias has nothing else, and
    # is not a thing a caller can subscribe to or address by id.
    aliases = serializers.SlugRelatedField(many=True, slug_field="slug", read_only=True)
    # Always false here: a retired subject is served by RetiredSubjectSerializer
    # instead. Carried so that a caller can tell the two shapes apart by reading one
    # field rather than by noticing which keys are missing.
    retired = serializers.BooleanField(source="is_retired", read_only=True)

    class Meta(SubjectSerializer.Meta):
        fields = [
            *SubjectSerializer.Meta.fields,
            "retired",
            "children",
            "aliases",
            "documents",
        ]
        read_only_fields = fields

    def get_documents(self, obj) -> list[str]:
        """The documents assigned to this subject, and not to those beneath it.

        Unchanged in meaning, deliberately. Red consumes this array and the
        precomputer keys document_meta off it, so widening it to the subtree would
        be a contract change dressed up as a bug fix. The subtree is the index
        file's business.
        """
        return [assignment.doc for assignment in obj.assignments.all()]

    def get_children(self, obj) -> list[str]:
        return [child.slug for child in obj.live_children.order_by("path")]


class SubjectAliasSerializer(serializers.ModelSerializer):
    """An alias, as the only thing an alias is for: the name it resolves to.

    Not the subject's own payload served under the alias's URL. That would answer
    the read in one fetch, at the cost of publishing the same subject under two
    addresses with nothing saying which one is canonical, and canonicalising a link
    is the whole reason a caller asked. So the same stub shape a retired subject
    gets, and callers tell the shapes apart by which key is present.
    """

    alias_of = serializers.SlugRelatedField(
        source="subject", slug_field="slug", read_only=True
    )

    class Meta:
        model = SubjectAlias
        fields = ["slug", "alias_of"]
        read_only_fields = fields


class RetiredSubjectSerializer(serializers.ModelSerializer):
    """A retired subject, as the only thing a retired subject is still for.

    Deliberately not the detail shape. A retired subject is not offered, does not
    appear in the vocabulary, and should not be rendered as though it were current;
    what is left is enough to redirect a link that names it. Callers tell the two
    apart by `retired`, which the live shape also carries.
    """

    merged_into = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    retired = serializers.BooleanField(source="is_retired", read_only=True)

    class Meta:
        model = Subject
        fields = ["slug", "retired", "merged_into"]
        read_only_fields = fields
