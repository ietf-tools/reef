# Copyright The IETF Trust 2026, All Rights Reserved
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from docsets.models import DocumentSet

from .models import Subscription, normalize_params


class SubscriptionSerializer(serializers.ModelSerializer):
    set = serializers.PrimaryKeyRelatedField(
        source="document_set",
        queryset=DocumentSet.objects.none(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Subscription
        fields = ["id", "kind", "params", "set", "verified", "created_at"]
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
        """Check and canonicalize the params, and the set the kind implies."""
        kind = attrs.get("kind")
        try:
            attrs["params"] = normalize_params(kind, attrs.get("params") or {})
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"params": exc.messages}) from exc

        document_set = attrs.get("document_set")
        if kind == Subscription.Kind.SET and document_set is None:
            raise serializers.ValidationError({"set": "The set kind requires a set."})
        if kind != Subscription.Kind.SET and document_set is not None:
            raise serializers.ValidationError(
                {"set": f"The {kind} kind does not take a set."}
            )
        return attrs
