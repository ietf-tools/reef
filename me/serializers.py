# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework import serializers

from docsets.models import DocumentSet


class MyDocumentSetSerializer(serializers.ModelSerializer):
    """One of the caller's sets, without its membership.

    Deliberately not docsets.DocumentSetSerializer: that one carries the set's
    whole `documents` array, which is the thing this endpoint exists to avoid
    sending. A client drawing "which of my sets hold this document" needs every
    set the caller owns to label the rows, but it only needs membership for the
    documents it is asking about — and that arrives per document as
    `your_set_ids`. Sending both would make the payload grow with the caller's
    library rather than with the page.
    """

    class Meta:
        model = DocumentSet
        fields = ["id", "title", "description", "created_at", "updated_at"]
        read_only_fields = fields


class MyDocumentSerializer(serializers.Serializer):
    """What one document is to the caller: their rating, their subscription,
    their sets.

    Nothing public: no average, no count, no subscriber total. Those are the
    same for everybody, so Red takes them from the data it already has for the
    route rather than from a per-caller request.
    """

    doc = serializers.CharField()
    # Null when the caller has not rated this document, which is a different
    # thing from a zero: there is no such rating.
    your_rating = serializers.IntegerField(allow_null=True)
    # The id of the caller's `rfc`-kind subscription to this document, null if
    # they have none. An id rather than a boolean because deleting a
    # subscription is by id, and this is the only read that would supply one.
    your_subscription_id = serializers.IntegerField(allow_null=True)
    # Ids of the caller's own sets holding this document. Empty, not null, when
    # none do.
    your_set_ids = serializers.ListField(child=serializers.UUIDField())


class MyDocumentsSerializer(serializers.Serializer):
    """The whole response: the caller's sets, and a row per requested document."""

    sets = MyDocumentSetSerializer(many=True)
    documents = MyDocumentSerializer(many=True)
