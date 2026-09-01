# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework import serializers

from .models import Subject, SubjectAlias


class SubjectSerializer(serializers.ModelSerializer):
    """A subject as a caller sees it, without its membership.

    The id is carried as well as the slug because subscribing names the id:
    the subscription holds a foreign key so that renaming a subject cannot
    detach its subscribers, and the id is the half of a subject's identity
    that a rename does not touch.
    """

    class Meta:
        model = Subject
        fields = ["id", "slug", "name", "description"]
        read_only_fields = fields


class SubjectDetailSerializer(SubjectSerializer):
    """A subject and the documents carrying it.

    Separate from the list serializer for the reason me.MyDocumentSetSerializer
    is separate from docsets.DocumentSetSerializer: a picker needs every
    subject and no membership, so sending membership in the list would make
    the payload grow with the whole catalogue rather than with the vocabulary.
    """

    documents = serializers.SerializerMethodField()
    # The other names this subject answers to, so that a picker can match what a
    # reader typed without a second read. Names only: an alias has nothing else, and
    # is not a thing a caller can subscribe to or address by id.
    aliases = serializers.SlugRelatedField(many=True, slug_field="slug", read_only=True)
    # Always false here: a retired subject is served by RetiredSubjectSerializer
    # instead. Carried so that a caller can tell the two shapes apart by reading one
    # field rather than by noticing which keys are missing.
    retired = serializers.BooleanField(source="is_retired", read_only=True)

    class Meta(SubjectSerializer.Meta):
        fields = [*SubjectSerializer.Meta.fields, "retired", "aliases", "documents"]
        read_only_fields = fields

    def get_documents(self, obj) -> list[str]:
        return [assignment.doc for assignment in obj.assignments.all()]


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
