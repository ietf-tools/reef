# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from docsets.models import DocumentSet
from subjects.models import Subject

from .models import Subscription, WebNotification, normalize_params, relation_problems

# What each relation field is called on the wire. The model field names read
# oddly to a caller, since nothing outside Reef calls a set a "document set",
# and `set` shadows a builtin, which is why the model does not use it.
RELATION_FIELD_NAMES = {"document_set": "set", "subject": "subject"}


class MinimalSubjectSerializer(serializers.ModelSerializer):
    """The fields a subscription list needs to show and link a subject.

    Avoid SubjectSerializer: its document counts run two queries per subject
    when nothing has precomputed them.
    """

    class Meta:
        model = Subject
        fields = ["slug", "name", "path"]
        read_only_fields = fields


class SubscriptionSerializer(serializers.ModelSerializer):
    set = serializers.PrimaryKeyRelatedField(
        source="document_set",
        queryset=DocumentSet.objects.none(),
        required=False,
        allow_null=True,
    )
    # Not scoped, unlike the set above: the vocabulary is public and curated,
    # so every subject is subscribable by anyone and there is nothing here for
    # a queryset to hide. Named by id rather than slug because the id is the
    # half of a subject's identity a rename does not touch, which is the whole
    # reason this is a relation and not a params key.
    subject = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.all(),
        required=False,
        allow_null=True,
    )
    subject_details = MinimalSubjectSerializer(source="subject", read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id",
            "kind",
            "params",
            "set",
            "subject",
            "subject_details",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # You can only subscribe to your own sets, so someone else's set is
        # indistinguishable from one that does not exist, and so is one staff
        # have taken down, which the default manager leaves out.
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            self.fields["set"].queryset = DocumentSet.objects.filter(owner=request.user)

    def validate(self, attrs):
        """Check and canonicalize the params, and the relation the kind implies."""
        kind = attrs.get("kind")
        try:
            attrs["params"] = normalize_params(kind, attrs.get("params") or {})
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"params": exc.messages}) from exc

        values = {field: attrs.get(field) for field in Subscription.RELATIONS.values()}
        if problems := {
            RELATION_FIELD_NAMES[field]: message
            for field, message in relation_problems(kind, values)
        }:
            raise serializers.ValidationError(problems)
        return attrs


@extend_schema_field(
    {
        "type": "object",
        "description": (
            "The event as stored. Every key but rfc is untyped here. rfc is on an "
            "RFC change alone, and only when Red had an entry for the document at "
            "the time."
        ),
        "properties": {
            "rfc": {
                "type": "object",
                "description": (
                    "Red's own entry for the document from rfc-index.json, an "
                    "RfcCommon, passed on as Red published it. Only number is relied "
                    "on; every other field is Red's to define."
                ),
                "properties": {"number": {"type": "integer"}},
                "required": ["number"],
                "additionalProperties": True,
            },
        },
        "additionalProperties": True,
    }
)
class WebNotificationEventField(serializers.JSONField):
    """An event served exactly as stored, whatever keys it holds."""


class WebNotificationSerializer(serializers.ModelSerializer):
    """One row of the caller's own notification feed.

    subscription_ids stays off the wire: nothing on Red needs it yet, and event
    already carries doc and url, everything a display needs to render and link.
    """

    event = WebNotificationEventField(read_only=True)

    class Meta:
        model = WebNotification
        fields = ["id", "kind", "event", "read", "created_at"]
        read_only_fields = fields


class DigestPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = get_user_model()
        fields = ["receive_digest_email"]
