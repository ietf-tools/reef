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
    owner_name = serializers.CharField(source="owner.name", read_only=True)

    class Meta:
        model = DocumentSet
        fields = [
            "id",
            "title",
            "slug",
            "description",
            "visibility",
            "owner_name",
            "documents",
            "created_at",
            "updated_at",
        ]
        # The slug is derived from the title, and membership is changed through
        # the documents endpoints rather than by rewriting the set.
        read_only_fields = ["id", "slug", "documents", "created_at", "updated_at"]

    def validate_visibility(self, value):
        """Refuse private, on create and on update alike.

        Rejected rather than silently coerced: a client that asked for a private
        set and got a 201 would have no way to tell that what it made is
        published. Checked here rather than in perform_create so that the
        update paths are covered by the same rule, since otherwise a set could
        be created public and immediately made private.

        An omitted visibility needs nothing here: the model default is public,
        so the private that remains is only reachable through the admin.
        """
        if value != DocumentSet.Visibility.PUBLIC:
            raise serializers.ValidationError(
                "Sets are public through the API. Private sets are not offered "
                "here, so visibility can only be set to public."
            )
        return value


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
