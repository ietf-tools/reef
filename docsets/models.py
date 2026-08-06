# Copyright The IETF Trust 2026, All Rights Reserved
from django.conf import settings
from django.db import models
from django.utils.text import slugify

from reef.docids import DOC_ID_MAX_LENGTH, normalize_doc_id

TITLE_MAX_LENGTH = 200


class DocumentSet(models.Model):
    """A user's named list of published documents, made in Red.

    A set is the unit a notification can be about: one subscription to a set
    stands in for a subscription to each document in it, and the membership can
    change afterwards without the subscriber doing anything.
    """

    class Visibility(models.TextChoices):
        PRIVATE = "private", "Private"
        PUBLIC = "public", "Public"

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
        default=Visibility.PRIVATE,
        help_text="A set title and its membership say what someone is tracking, "
        "so publishing is the owner's choice.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
        siblings = DocumentSet.objects.filter(owner_id=self.owner_id).exclude(
            pk=self.pk
        )
        slug, suffix = base, 1
        while siblings.filter(slug=slug).exists():
            suffix += 1
            slug = f"{base}-{suffix}"
        return slug


# For SPECTACULAR_SETTINGS["ENUM_NAME_OVERRIDES"], which resolves an import
# path and so cannot reach an attribute of a nested class. Without the override
# the generated enum is named from a hash of the choices, because Survey has a
# visibility field too.
DOCUMENT_SET_VISIBILITY_CHOICES = DocumentSet.Visibility.choices


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
