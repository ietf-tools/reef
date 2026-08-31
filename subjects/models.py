# Copyright The IETF Trust 2026, All Rights Reserved
"""Reef's subject vocabulary, and which documents carry each subject.

A subject is a curated topic an RFC can be about: "security", "routing",
"congestion control". Both halves live here, the list of subjects that exist
and the assignment of a subject to a document, because the vocabulary is
Reef's own rather than something read out of the datatracker.

This is the one place Reef holds something about a document beyond its
identifier, and it does not contradict the rule that it holds no document
metadata (see plan.md). That rule was argued from staleness: a title or a
status copied here would drift from the datatracker's. A subject has no
upstream to drift from. Reef is where it is decided, so Reef is where it is
kept, and no other system has an opinion for this one to disagree with.

What Reef still does not hold is a document's title, status, or existence. A
subject can be assigned to an identifier that names nothing; the assignment is
a curation error rather than something the database can catch, exactly as a
rating or a set entry naming a nonexistent RFC already is.
"""

from django.db import models
from django.utils import timezone

from reef.docids import DOC_ID_MAX_LENGTH, normalize_doc_id

NAME_MAX_LENGTH = 100
SLUG_MAX_LENGTH = 50


class SubjectQuerySet(models.QuerySet):
    def live(self):
        return self.filter(retired_at__isnull=True)

    def retired(self):
        return self.filter(retired_at__isnull=False)


class LiveSubjectManager(models.Manager.from_queryset(SubjectQuerySet)):
    """The default manager, which does not see retired subjects.

    Following docsets.DocumentSet: the read paths that offer a subject to somebody
    should not have to remember to exclude the retired ones, and the few places that
    need them ask for all_objects by name. It cannot be the base manager, because
    related lookups use that and a subscription must keep matching through the
    subject it points at even after the subject is retired -- that is the whole
    point of retiring rather than deleting.
    """

    def get_queryset(self):
        return super().get_queryset().live()


class Subject(models.Model):
    """One topic in the curated vocabulary, maintained by staff in the admin.

    Short and slow-moving, like popularity.PopularEntry and unlike a document
    set: nobody self-serves a subject into existence, so the list stays small
    enough to hand to a caller whole.

    A subject has two identities and needs both. The primary key is what a
    subscription points at, so that renaming a subject does not silently
    detach its subscribers. The slug is what a caller addresses it by and what
    Red puts in a URL its readers see.

    That is the opposite of the call made for document sets, which dropped
    their slug, and the difference is who names the thing. A set is titled by
    its owner, so titles collide, change often, and slugify to nothing often
    enough to need a fallback and a suffixing loop. A subject is named once by
    staff, and two subjects sharing a name is a curation mistake to be
    prevented rather than a case to be tolerated. So the uniqueness that cost
    document sets too much is the point here.
    """

    slug = models.SlugField(
        max_length=SLUG_MAX_LENGTH,
        unique=True,
        help_text="Stable identifier used in URLs and by Red. Changing it "
        "breaks links that name the old one; the name is the field to edit "
        "when the wording is what changed.",
    )
    name = models.CharField(
        max_length=NAME_MAX_LENGTH,
        unique=True,
        help_text="How the subject is shown to readers.",
    )
    description = models.TextField(
        blank=True,
        help_text="What belongs under this subject, for whoever curates it "
        "next and for a caller drawing a picker.",
    )
    # Retired, not deleted. A vocabulary changes, and a subject somebody follows
    # cannot simply go: deleting one used to cascade its subscriptions away, which
    # silently stopped mail that a reader had asked for. Retiring takes it out of the
    # picker and refuses new subscribers while leaving the existing ones matching, so
    # the population decays rather than being cut off. Clearing this restores it.
    retired_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this subject stopped being offered. Clear it to bring the "
        "subject back; existing subscriptions keep working either way.",
    )
    # Where its followers went, when it was retired by being merged. Kept so that a
    # link naming the old subject can be redirected rather than broken, which is the
    # only reason a retired subject is still published at all.
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merged_from",
        help_text="Set by a merge. The subject this one's documents and followers "
        "were moved to.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LiveSubjectManager()
    all_objects = models.Manager.from_queryset(SubjectQuerySet)()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (retired)" if self.is_retired else self.name

    @property
    def is_retired(self):
        return self.retired_at is not None

    def retire(self, merged_into=None):
        self.retired_at = timezone.now()
        self.merged_into = merged_into
        self.save(update_fields=["retired_at", "merged_into", "updated_at"])

    def unretire(self):
        """Bring a subject back. Retired means retired until this is called."""
        self.retired_at = None
        self.merged_into = None
        self.save(update_fields=["retired_at", "merged_into", "updated_at"])


class SubjectAssignment(models.Model):
    """One document carrying one subject.

    A separate row per pair rather than a list on either side: this is the
    join a subscription match runs through, so it has to be indexable from the
    document end, which is the end an incoming change event arrives at.

    Assignment is a curation act with no history kept beyond when it happened.
    Unassigning is a hard delete, in the way unsubscribing is: there is no
    state between assigned and not.
    """

    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="assignments"
    )
    doc = models.CharField(max_length=DOC_ID_MAX_LENGTH, db_index=True)
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["doc"]
        constraints = [
            models.UniqueConstraint(
                fields=["subject", "doc"], name="unique_document_per_subject"
            )
        ]

    def __str__(self):
        return f"{self.subject_id}: {self.doc}"

    def save(self, *args, **kwargs):
        # Canonical form, so that an assignment can be joined to the same
        # document's ratings, sets and subscriptions. No default series: a
        # subject can be assigned to any published series, so "14" has to be
        # written out as rfc14 or bcp14.
        self.doc = normalize_doc_id(self.doc)
        super().save(*args, **kwargs)
