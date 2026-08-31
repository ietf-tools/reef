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
        # Predicates over the event: they say what has to have happened, not
        # which document it happened to, so no join resolves them.
        NEW_RFC = "new_rfc", "Any new RFC"
        BY_STATUS = "by_status", "New RFC by status"
        OBSOLETED = "obsoleted", "RFC obsoleted or made historic"
        # The rest name documents, one way or another, and are matched by
        # subscriptions_for_document.
        RFC = "rfc", "Changes to one specific RFC"
        SET = "set", "Changes to anything in a document set"
        SUBJECT = "subject", "Changes to anything carrying a subject"

    # The parameters each kind takes. Every listed key is required and no other
    # key is accepted: uniqueness compares the stored JSON, so a stray key would
    # quietly make a duplicate subscription look distinct.
    PARAMS_KEYS = {
        Kind.NEW_RFC: (),
        Kind.BY_STATUS: ("status",),
        Kind.OBSOLETED: (),
        Kind.RFC: ("rfc",),
        Kind.SET: (),  # the set is a foreign key, not a parameter
        Kind.SUBJECT: (),  # and so is the subject
    }

    # The kinds that point at a row rather than describing one in params, and
    # the field each points through. Exactly one of these is filled for a
    # subscription of that kind and all of them are null for every other kind,
    # which is what the uniqueness constraint below is written against.
    RELATIONS = {
        Kind.SET: "document_set",
        Kind.SUBJECT: "subject",
    }

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    params = models.JSONField(default=dict, blank=True)
    # The set and subject kinds point at a row rather than naming one in
    # params: a title or a slug in JSON has no referential integrity, so it
    # would break on a rename and leave a silently dead subscription on
    # delete. Both are nullable because only their own kind fills them.
    document_set = models.ForeignKey(
        "docsets.DocumentSet",
        on_delete=models.CASCADE,
        related_name="subscriptions",
        null=True,
        blank=True,
    )
    subject = models.ForeignKey(
        "subjects.Subject",
        # Protected, not cascaded. Deleting a subject used to take its subscriptions
        # with it, which silently stopped mail somebody had asked for; now a subject
        # with followers cannot be deleted at all, and has to be retired or merged
        # instead. Django's admin reports the refusal rather than failing.
        on_delete=models.PROTECT,
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
                fields=["user", "kind", "params", "document_set", "subject"],
                name="unique_subscription_per_user",
                # Every relation column is null for every kind but its own, and
                # Postgres counts nulls as distinct by default, which would stop
                # the constraint blocking duplicates of all the other kinds.
                # Each further relation added here makes that more load-bearing,
                # not less.
                nulls_distinct=False,
            )
        ]

    def __str__(self):
        return f"{self.user_id}: {self.kind}"

    def save(self, *args, **kwargs):
        # Every write path normalizes, not just the API: the constraint compares
        # stored bytes, so the admin and the ingest task have to agree with it.
        self.params = normalize_params(self.kind, self.params)
        problem = next(iter(relation_problems(self.kind, self.relations())), None)
        if problem is not None:
            raise ValidationError(problem[1])
        super().save(*args, **kwargs)

    def relations(self):
        """What this subscription fills each relation field with, by name."""
        return {
            field: getattr(self, f"{field}_id") for field in self.RELATIONS.values()
        }


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


def relation_problems(kind, values):
    """Yield (field, message) for every relation this kind fills wrongly.

    `values` maps each relation field name to what the subscription holds
    there. Checked on every write path, for the same reason params are
    normalized on every write path: the uniqueness constraint is written as
    though a kind's own relation is the only one filled, so a row filling two
    would be compared against a shape nothing else writes.

    Yields rather than raises so that each caller reports in its own idiom:
    the model raises a plain ValidationError, and the serializer keys the
    message to the field the caller sent.
    """
    for relation_kind, field in Subscription.RELATIONS.items():
        present = values.get(field) is not None
        if kind == relation_kind and not present:
            yield field, f"The {kind} kind requires a {relation_kind}."
        elif kind != relation_kind and present:
            yield field, f"The {kind} kind does not take a {relation_kind}."


class DocumentSnapshot(models.Model):
    """The state of Red's index that this deployment has already notified about.

    Not document metadata, despite holding some. Nothing reads it to answer a
    question about a document; it exists only to be compared against the current
    index, and every value in it is replaced wholesale on each run. Reef's rule that
    it stores identifiers and nothing else is about what it treats as true, and this
    is never treated as true — it is a record of what has already been said.

    One row, pinned to a fixed primary key so a second cannot appear and there is no
    "which one is current" to get wrong.

    The payload is zlib-compressed JSON rather than a JSONField, and one blob rather
    than a row per document, for reasons worth keeping: the watched-field set will
    change and a column-per-field table would need a migration each time, while a
    queryable table of statuses is exactly the second copy of document metadata that
    something eventually reads as truth. Compressed it is 57 KiB against 878 KiB.
    """

    SINGLETON_PK = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_PK)
    # {doc_id: {status, obsoleted_by, updates, updated_by, subseries}}, compressed.
    payload = models.BinaryField()
    # The index's own createdOn, so a run can tell that Red has not republished since
    # last time, which produces no diff and therefore no mail.
    created_on = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1), name="document_snapshot_singleton"
            )
        ]

    def __str__(self):
        return f"Document snapshot of {self.created_on}, taken {self.updated_at}"


class PendingNotification(models.Model):
    """One digest that is owed to one subscriber, written down before it is queued.

    Celery alone will not do here. Reef's broker has no persistent volume, so a
    RabbitMQ restart drops every queued message, and a notification lost that way is
    lost silently: nothing else records that a change was matched to a reader. Putting
    the row in Postgres first moves the guarantee to where the rest of Reef's data
    already lives, and follows Purple, which writes a MailMessage and passes the task
    only its primary key.

    Unlike Purple's, this holds the arguments rather than the rendered message. Purple
    composes a message at a moment and should send exactly that; here the body is
    derived from data that is still in the database, so re-rendering at send time is
    both cheaper to store and correct in the case that matters -- a reader who
    unsubscribes between the write and the send does not get the mail, because the
    render looks their subscriptions up again.

    A row lives only as long as the obligation it records. It is deleted once the
    notification has gone out, as Purple deletes its MailMessage, because this is a
    queue and not a log: what is left in the table is what is still owed, plus the few
    that could never be delivered, which are worth keeping precisely because they are
    the record that somebody did not hear about something.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    # The subscriptions that matched, so the message can say why it arrived. Ids
    # rather than a relation: a subscription deleted before the send should drop out
    # of the reasons, not cascade the notification away with it.
    subscription_ids = models.JSONField(default=list)
    events = models.JSONField(default=list)
    attempts = models.PositiveSmallIntegerField(default=0)
    # Set immediately before the row is deleted, and so almost never seen. It exists
    # to cover the gap between sending and deleting: a crash in there must leave a row
    # that is skipped rather than one that is sent again.
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            # The sweeper's query: what is still owed, oldest first.
            models.Index(
                fields=["sent_at", "created_at"], name="notification_unsent_idx"
            )
        ]

    def __str__(self):
        state = f"sent {self.sent_at}" if self.sent_at else "unsent"
        return f"Notification to {self.user} of {len(self.events)} change(s), {state}"
