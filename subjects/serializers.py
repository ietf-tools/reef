# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework import serializers

from .models import Subject


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

    class Meta(SubjectSerializer.Meta):
        fields = [*SubjectSerializer.Meta.fields, "documents"]
        read_only_fields = fields

    def get_documents(self, obj) -> list[str]:
        return [assignment.doc for assignment in obj.assignments.all()]
