# Copyright The IETF Trust 2026, All Rights Reserved
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from reef.docids import normalize_doc_id

from .models import DocumentSet, DocumentSetEntry


def canonical_doc_id(value):
    """Canonicalize an identifier, raising a DRF error so callers get a 400."""
    try:
        return normalize_doc_id(value)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(exc.messages) from exc


class DocumentSetEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentSetEntry
        fields = ["doc", "rank", "added_at"]
        read_only_fields = fields


class DocumentSetSerializer(serializers.ModelSerializer):
    documents = DocumentSetEntrySerializer(source="entries", many=True, read_only=True)

    class Meta:
        model = DocumentSet
        fields = [
            "id",
            "title",
            "description",
            "documents",
            "created_at",
            "updated_at",
        ]
        # Membership is changed through the documents endpoints rather than by
        # rewriting the set.
        read_only_fields = ["id", "documents", "created_at", "updated_at"]
        # There is no visibility field, here or on the model: a set is made to
        # be shared, and anyone holding its unguessable id can read it. Staff
        # moderation is the soft delete, which is not in the API either, so a
        # set staff have taken down cannot be restored by its owner.
        #
        # The owner is not in the response either. A set read is anonymous, so
        # naming the owner would attach a person to a reading list for anyone
        # holding the link, which is more than the set itself says.


class DocumentSetOrderSerializer(serializers.Serializer):
    """The set's documents, in the order they should be shown."""

    documents = serializers.ListField(child=serializers.CharField(), allow_empty=True)

    def validate_documents(self, value):
        documents = [canonical_doc_id(doc) for doc in value]
        if len(set(documents)) != len(documents):
            raise serializers.ValidationError("A document is listed twice.")

        current = set(
            self.context["document_set"].entries.values_list("doc", flat=True)
        )
        if set(documents) != current:
            raise serializers.ValidationError(
                "List exactly the documents the set holds. Reordering neither "
                "adds nor removes; use the documents endpoint for that."
            )
        return documents
