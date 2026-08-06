# Copyright The IETF Trust 2026, All Rights Reserved
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from reef.docids import normalize_doc_id

PARAM_VALUE_MAX_LENGTH = 128


class Subscription(models.Model):
    """A user's subscription to RFC-series notifications.

    Scaffold: tied to an authenticated user. Email delivery and datatracker
    change ingestion are stubbed (see tasks.py) pending the full build.
    """

    class Kind(models.TextChoices):
        NEW_RFC = "new_rfc", "Any new RFC"
        BY_STATUS = "by_status", "New RFC by status"
        OBSOLETED = "obsoleted", "RFC obsoleted or made historic"
        SUBJECT_TAG = "subject_tag", "RFC with a subject tag"
        RFC = "rfc", "Changes to one specific RFC"
        SET = "set", "Changes to anything in a document set"

    # The parameters each kind takes. Every listed key is required and no other
    # key is accepted: uniqueness compares the stored JSON, so a stray key would
    # quietly make a duplicate subscription look distinct.
    PARAMS_KEYS = {
        Kind.NEW_RFC: (),
        Kind.BY_STATUS: ("status",),
        Kind.OBSOLETED: (),
        Kind.SUBJECT_TAG: ("tag",),
        Kind.RFC: ("rfc",),
        Kind.SET: (),  # the set is a foreign key, not a parameter
    }

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    params = models.JSONField(default=dict, blank=True)
    # The set kind points at a set rather than naming one in params: a slug in
    # JSON has no referential integrity, so it would break on a retitle and
    # leave a silently dead subscription on delete.
    document_set = models.ForeignKey(
        "docsets.DocumentSet",
        on_delete=models.CASCADE,
        related_name="subscriptions",
        null=True,
        blank=True,
    )
    verified = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "kind", "params", "document_set"],
                name="unique_subscription_per_user",
                # document_set is null for every kind but set, and Postgres
                # counts nulls as distinct by default, which would stop the
                # constraint blocking duplicates of all the other kinds.
                nulls_distinct=False,
            )
        ]

    def __str__(self):
        return f"{self.user_id}: {self.kind}"

    def save(self, *args, **kwargs):
        # Every write path normalizes, not just the API: the constraint compares
        # stored bytes, so the admin and the ingest task have to agree with it.
        self.params = normalize_params(self.kind, self.params)
        if self.kind == self.Kind.SET and self.document_set_id is None:
            raise ValidationError("The set kind requires a set.")
        if self.kind != self.Kind.SET and self.document_set_id is not None:
            raise ValidationError(f"The {self.kind} kind does not take a set.")
        super().save(*args, **kwargs)


def normalize_params(kind, params):
    """Return params in canonical form, or raise ValidationError.

    Called from Subscription.save() and from the serializer. Unknown and
    missing keys are rejected rather than ignored, so that two subscriptions
    are equal exactly when they mean the same thing.
    """
    if not isinstance(params, dict):
        raise ValidationError("params must be an object.")

    expected = Subscription.PARAMS_KEYS.get(kind)
    if expected is None:
        raise ValidationError(f"Unknown subscription kind: {kind!r}.")

    if missing := sorted(set(expected) - set(params)):
        raise ValidationError(
            f"The {kind} kind requires {', '.join(missing)} in params."
        )
    if unknown := sorted(set(params) - set(expected)):
        raise ValidationError(
            f"The {kind} kind does not take {', '.join(unknown)} in params."
            + (f" It takes {', '.join(expected)}." if expected else "")
        )

    normalized = {}
    for key in expected:
        value = params[key]
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"params.{key} must be a non-empty string.")
        if len(value) > PARAM_VALUE_MAX_LENGTH:
            raise ValidationError(
                f"params.{key} must be at most {PARAM_VALUE_MAX_LENGTH} characters."
            )
        if key == "rfc":
            # No default series: a subscription can name any published series,
            # so a bare number has to be written out as rfc9110 or bcp14.
            normalized[key] = normalize_doc_id(value)
        else:
            normalized[key] = value.strip().lower()
    return normalized
