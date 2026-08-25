# Copyright The IETF Trust 2026, All Rights Reserved
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from docsets.models import DocumentSet
from subjects.models import Subject

from .models import Subscription, normalize_params, relation_problems

# What each relation field is called on the wire. The model field names read
# oddly to a caller, since nothing outside Reef calls a set a "document set",
# and `set` shadows a builtin, which is why the model does not use it.
RELATION_FIELD_NAMES = {"document_set": "set", "subject": "subject"}


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

    class Meta:
        model = Subscription
        fields = ["id", "kind", "params", "set", "subject", "verified", "created_at"]
        read_only_fields = ["id", "verified", "created_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # You can only subscribe to your own sets for now, so someone else's set
        # is indistinguishable from one that does not exist, and so is one staff
        # have taken down, which the default manager leaves out. Opening this up
        # means rechecking at send time that the set is still there; see plan.md
        # open items.
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
