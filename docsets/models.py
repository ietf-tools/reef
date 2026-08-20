# Copyright The IETF Trust 2026, All Rights Reserved
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from reef.docids import DOC_ID_MAX_LENGTH, normalize_doc_id

TITLE_MAX_LENGTH = 200


class DocumentSetQuerySet(models.QuerySet):
    """Sets split by the soft-delete state, for callers that want one side."""

    def live(self):
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        return self.filter(deleted_at__isnull=False)


class LiveDocumentSetManager(models.Manager.from_queryset(DocumentSetQuerySet)):
    """Sees live sets only. The default manager, so that omission is safe.

    A deleted set has to be indistinguishable from one that never existed, and
    the way that fails is a query that forgets to exclude it: the set comes
    back, nothing errors, and the takedown is silently undone in one read path.
    Making the exclusion the default inverts that, so a caller has to ask for
    the deleted rows by name through all_objects. It cannot be the base manager
    too: a Subscription's set has to keep resolving after a takedown, or the
    send path could not tell a deleted set from a broken foreign key.
    """

    def get_queryset(self):
        return super().get_queryset().live()


class DocumentSet(models.Model):
    """A user's named list of published documents, made in Red.

    A set is the unit a notification can be about: one subscription to a set
    stands in for a subscription to each document in it, and the membership can
    change afterwards without the subscriber doing anything.
    """

    class Visibility(models.TextChoices):
        PRIVATE = "private", "Private"
        PUBLIC = "public", "Public"

    # A random id, not a sequence: a set is public by default and its URL is
    # handed around, so a sequential id would let anyone walk /sets/1, /sets/2
    # and read every set in the system. Unguessability is the only thing
    # standing between a published set and enumeration of all of them.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="document_sets",
    )
    title = models.CharField(max_length=TITLE_MAX_LENGTH)
    slug = models.SlugField(max_length=TITLE_MAX_LENGTH, editable=False)
    description = models.TextField(blank=True)
    visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
        help_text="Sets are public: a title and its membership are readable by "
        "anyone with the link. Private is not offered through the API; it is "
        "kept for staff to unpublish a set from here.",
    )
    # Soft delete is the staff takedown, and it is a third state, not a
    # stronger private: an unpublished set is still the owner's to read and
    # edit, whereas a deleted one is gone for everybody, the owner included,
    # and every read path answers as if it had never existed. The rows are kept
    # so that a takedown can be reviewed and reversed, and so that enforcing
    # one does not destroy a user's data.
    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Blank for a live set. Set it to take the set down: it then "
        "404s everywhere, for its owner as much as for anyone else. Clear it "
        "to restore.",
    )
    deleted_reason = models.TextField(
        blank=True,
        help_text="Why the set was taken down, for whoever reviews the "
        "decision later. Never served through the API, and optional: a "
        "takedown that needs doing now should not wait on the wording.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LiveDocumentSetManager()
    all_objects = models.Manager.from_queryset(DocumentSetQuerySet)()

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "slug"], name="unique_document_set_slug_per_owner"
            )
        ]

    def __str__(self):
        return f"{self.title} ({self.owner_id})"

    @property
    def is_public(self):
        return self.visibility == self.Visibility.PUBLIC

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    def soft_delete(self, reason=""):
        """Take the set down, keeping its rows and its membership."""
        self.deleted_at = timezone.now()
        self.deleted_reason = reason
        self.save(update_fields=["deleted_at", "deleted_reason", "updated_at"])

    def restore(self):
        """Put a taken-down set back, and drop the reason it was taken down."""
        self.deleted_at = None
        self.deleted_reason = ""
        self.save(update_fields=["deleted_at", "deleted_reason", "updated_at"])

    def save(self, *args, **kwargs):
        # The slug is derived, never given: identity lives in the id, so a
        # shared link survives a retitle and the slug is free to follow the
        # title. See PublicDocumentSetDetail, which redirects a stale one.
        self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self):
        # A title of only punctuation or of a script slugify strips leaves an
        # empty slug, which would make an unreadable URL and collide with every
        # other such set the owner has.
        base = slugify(self.title) or "set"
        # all_objects: a deleted set still holds its row, and so its (owner,
        # slug) pair. Skipping it here would hand the same slug out twice and
        # break the unique constraint on the way to a 500.
        siblings = DocumentSet.all_objects.filter(owner_id=self.owner_id).exclude(
            pk=self.pk
        )
        slug, suffix = base, 1
        while siblings.filter(slug=slug).exists():
            suffix += 1
            slug = f"{base}-{suffix}"
        return slug


class DocumentSetEntry(models.Model):
    """One document in a set, at a display position."""

    document_set = models.ForeignKey(
        DocumentSet, on_delete=models.CASCADE, related_name="entries"
    )
    doc = models.CharField(max_length=DOC_ID_MAX_LENGTH, db_index=True)
    rank = models.PositiveIntegerField(default=0, help_text="Lower sorts first")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["rank", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["document_set", "doc"], name="unique_document_per_set"
            )
        ]
        verbose_name_plural = "document set entries"

    def __str__(self):
        return f"{self.document_set_id}: {self.doc}"

    def save(self, *args, **kwargs):
        # No default series: a set holds documents from any series, so "14" has
        # to be written out as rfc14 or bcp14.
        self.doc = normalize_doc_id(self.doc)
        super().save(*args, **kwargs)
