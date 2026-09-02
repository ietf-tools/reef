# Copyright The IETF Trust 2026, All Rights Reserved
"""Reef's subject vocabulary, and which documents carry each subject.

A subject is a curated topic an RFC can be about: "security", "routing",
"congestion control". Both halves live here, the list of subjects that exist
and the assignment of a subject to a document, because the vocabulary is
Reef's own rather than something read out of the datatracker. So does a third,
smaller thing: the other names a subject answers to, which exist because Reef
publishes the names and so has to go on resolving the ones it has published.

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

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from reef.docids import DOC_ID_MAX_LENGTH, normalize_doc_id

NAME_MAX_LENGTH = 100
SLUG_MAX_LENGTH = 50

# The vocabulary is a tree, four levels at the deepest:
# messaging / email / email-authentication / dkim. The ceiling is agreed rather
# than observed, and it is worth having as a number the model enforces: it bounds
# the length of a path, and it means no read ever has to recurse without a limit.
MAX_DEPTH = 4
PATH_SEPARATOR = "/"
# Four slugs and the separators between them, with room to spare.
PATH_MAX_LENGTH = 255


class SubjectQuerySet(models.QuerySet):
    def live(self):
        return self.filter(retired_at__isnull=True)

    def retired(self):
        return self.filter(retired_at__isnull=False)

    def roots(self):
        return self.filter(parent__isnull=True)

    def at_or_under(self, subject):
        """The subject and everything beneath it, in tree order.

        One indexed query rather than a walk, which is what the derived path column
        is for. The separator is appended to the prefix so that a sibling whose slug
        merely begins with this one's cannot be swept in: security/cryptography/
        does not match security/cryptography-x/.
        """
        prefix = subject.path + PATH_SEPARATOR
        return self.filter(
            models.Q(path=subject.path) | models.Q(path__startswith=prefix)
        ).order_by("path")

    def under(self, subject):
        """Everything beneath the subject, excluding the subject itself."""
        return self.filter(path__startswith=subject.path + PATH_SEPARATOR).order_by(
            "path"
        )


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
        "leaves the old one behind as an alias, so links naming it still "
        "resolve; the name is still the field to edit when only the wording "
        "changed.",
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
    # The subject this one sits under, and the whole of the hierarchy's truth. Not
    # to be confused with merged_into above, which is the other self-reference and
    # says something entirely different: merged_into is a redirect recording where a
    # retired subject's documents and followers went, while parent is containment.
    #
    # PROTECT rather than CASCADE, matching Subscription.subject: deleting a subject
    # that others sit under would take a branch of the vocabulary with it, and the
    # admin reports the refusal legibly. Retiring is the mechanism for taking a
    # subject out of use; deleting is for one created by mistake.
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
        help_text="The subject this one sits under. Leave empty for a top-level "
        "subject. A document assigned here also counts under every subject above.",
    )
    # Derived from parent and slug, maintained by save(), and never edited. It buys
    # three things a bare parent pointer does not: a listing in tree order from
    # order_by("path"), a subtree in one indexed query, and the ancestors of a
    # subject read straight off the string with no query at all.
    path = models.CharField(
        max_length=PATH_MAX_LENGTH,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Slugs from the top down, separated by a slash. Derived; edit the "
        "slug or the parent instead.",
    )
    # Also derived. Stored rather than counted from path at every use because it is
    # what the admin indents by and what selects the roots.
    depth = models.PositiveSmallIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = LiveSubjectManager()
    all_objects = models.Manager.from_queryset(SubjectQuerySet)()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (retired)" if self.is_retired else self.name

    # -- the tree ---------------------------------------------------------------

    @property
    def ancestor_slugs(self):
        """The slugs above this one, top first. Read off the path, no query."""
        return self.path.split(PATH_SEPARATOR)[:-1] if self.path else []

    @property
    def derived_path(self):
        """What path should be, given the parent and the slug."""
        if self.parent_id is None:
            return self.slug
        return f"{self.parent.path}{PATH_SEPARATOR}{self.slug}"

    def validate_tree(self):
        """Refuse a parent that cannot hold this subject.

        Called from clean() for the admin form and from save() for everything else,
        because reparenting happens in code as well -- a merge moves children, and
        the importer builds whole branches -- and an invariant only the form enforces
        is one the form is the only thing that cannot break.
        """
        if self.parent_id is None:
            return
        if self.parent_id == self.pk:
            raise ValidationError({"parent": "A subject cannot be its own parent."})
        # Walking up rather than down: the chain is at most MAX_DEPTH long when the
        # tree is sound, and the bound is what stops a cycle that somehow already
        # exists from spinning here forever.
        seen, ancestor = {self.pk}, self.parent
        for _ in range(MAX_DEPTH + 1):
            if ancestor is None:
                break
            if ancestor.pk in seen:
                raise ValidationError(
                    {"parent": f"{ancestor} is already beneath this subject."}
                )
            seen.add(ancestor.pk)
            ancestor = ancestor.parent
        else:
            raise ValidationError({"parent": "That parent forms a cycle."})

        if self.parent.is_retired:
            raise ValidationError(
                {
                    "parent": f"{self.parent} is retired, so a subject under it "
                    "would be offered without being reachable in a picker."
                }
            )

        # The ceiling is checked against the deepest descendant and not against this
        # subject, because moving a branch is what overflows it: a leaf can go
        # anywhere a parent has room, a three-deep branch cannot.
        height = 0
        if self.pk is not None and self.path:
            deepest = (
                Subject.all_objects.under(self)
                .order_by("-depth")
                .values_list("depth", flat=True)
                .first()
            )
            if deepest is not None:
                height = deepest - self.depth
        if self.parent.depth + 1 + height >= MAX_DEPTH:
            raise ValidationError(
                {
                    "parent": f"The vocabulary is {MAX_DEPTH} levels deep at most, "
                    f"and this would put {self.slug} at level "
                    f"{self.parent.depth + 2 + height}."
                }
            )

    def clean(self):
        """Refuse a slug that is already another subject's alias.

        Nothing breaks if one slips past: the detail read looks for a subject before
        it looks for an alias, so the alias would simply never be reached. But an
        unreachable alias is a curation mistake nobody would see, and this is the
        form staff type the slug into.
        """
        super().clean()
        self.validate_tree()
        # subject_id rather than subject, so this also works before the subject has
        # been saved, where a model instance in a related filter is refused.
        clash = SubjectAlias.objects.filter(slug=self.slug).exclude(subject_id=self.pk)
        if self.slug and clash.exists():
            raise ValidationError(
                {"slug": f"{self.slug} is already an alias of another subject."}
            )

    def save(self, *args, **kwargs):
        """Save, keeping path and depth derived, and leave the old slug behind as an
        alias if the slug changed.

        The alias is automatic rather than something staff do afterwards, because the
        cost of forgetting falls on readers following a link that has already been
        published and not on whoever renamed it. Deleting the alias is one click for
        the case the rename was fixing a typo nobody ever saw.

        The path is automatic for a harder reason: it is a denormalisation, so the
        only way it stays true is for nothing to be able to write the fields it
        derives from without it following. A rename or a move also rewrites every
        path beneath this one.
        """
        update_fields = kwargs.get("update_fields")
        # retire() and unretire() name their fields, so the writes that cannot move
        # a subject do not pay for the parent lookup or the subtree query.
        touches_path = update_fields is None or bool(
            {"slug", "parent", "parent_id"} & set(update_fields)
        )
        renamed_from = self._slug_before_this_save(update_fields)
        previous_path = None
        if touches_path:
            self.validate_tree()
            previous_path = self._path_before_this_save()
            self.path = self.derived_path
            self.depth = self.path.count(PATH_SEPARATOR)
            if update_fields is not None:
                kwargs["update_fields"] = list(
                    dict.fromkeys([*update_fields, "path", "depth"])
                )
        super().save(*args, **kwargs)
        if previous_path is not None and previous_path != self.path:
            self._repath_subtree(previous_path)
        if renamed_from is None:
            return
        # A subject renamed to one of its own aliases is swapping which name is
        # canonical, so that alias is consumed rather than left to shadow the slug.
        self.aliases.filter(slug=self.slug).delete()
        SubjectAlias.objects.create(slug=renamed_from, subject=self)

    def _slug_before_this_save(self, update_fields):
        """The slug this row had, if this save is changing it, else None."""
        if self.pk is None:
            return None
        if update_fields is not None and "slug" not in update_fields:
            # retire() and unretire() name their fields, so the common writes that
            # cannot be renames do not pay for a query to find that out.
            return None
        previous = (
            Subject.all_objects.filter(pk=self.pk)
            .values_list("slug", flat=True)
            .first()
        )
        return previous if previous not in (None, self.slug) else None

    def _path_before_this_save(self):
        """The path this row had, or None if it is being created."""
        if self.pk is None:
            return None
        return (
            Subject.all_objects.filter(pk=self.pk)
            .values_list("path", flat=True)
            .first()
        )

    def _repath_subtree(self, previous_path):
        """Rewrite the paths beneath this subject after it moved or was renamed.

        A prefix substitution per descendant rather than a walk, so the shape of the
        branch is irrelevant and the order rows are visited in does not matter.

        bulk_update, so no per-row post_save fires. That is deliberate and not a
        gap: the precomputer rebuilds the whole subjects task on any one signal, and
        this subject's own save has already sent one.
        """
        old_prefix = previous_path + PATH_SEPARATOR
        moved = list(Subject.all_objects.filter(path__startswith=old_prefix))
        for descendant in moved:
            tail = descendant.path[len(old_prefix) :]
            descendant.path = self.path + PATH_SEPARATOR + tail
            descendant.depth = descendant.path.count(PATH_SEPARATOR)
        if moved:
            Subject.all_objects.bulk_update(moved, ["path", "depth"])

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


class SubjectAlias(models.Model):
    """Another name for a subject, and never a subject itself.

    A name outlives the wording that produced it: a slug that was renamed, an
    abbreviation readers type, a term a survey audience was written against. Retiring
    a subject with merged_into set already redirects one name to another, but it says
    something else while doing it. A retired subject was real -- it had an id a
    subscription pointed at and documents under it, and its redirect records what
    became of them. An alias never was, so writing one as a retirement would mean
    creating a row with a subscribable id and a permanent history entry in order to
    say that one word means another.

    So an alias carries no identity at all: no id anybody points at, no assignments,
    no place in the vocabulary, and nothing to subscribe to. It can afford to be this
    thin because a subscription names a subject by primary key rather than by slug,
    which is the same decision that already made renaming safe for subscribers.
    """

    slug = models.SlugField(
        max_length=SLUG_MAX_LENGTH,
        unique=True,
        help_text="A name that resolves to this subject. Readers following a link "
        "that uses it are redirected to the subject's own slug.",
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="aliases"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["slug"]
        verbose_name_plural = "subject aliases"

    def __str__(self):
        return f"{self.slug} -> {self.subject.slug}"

    def clean(self):
        super().clean()
        if self.slug and Subject.all_objects.filter(slug=self.slug).exists():
            raise ValidationError(
                {
                    "slug": f"{self.slug} is a subject's own slug, so an alias of that "
                    "name would never be reached."
                }
            )

    def save(self, *args, **kwargs):
        # Enforced here and not only in clean() because aliases are created by code as
        # well as by staff -- a rename leaves one behind, a merge inherits them -- and
        # an alias a subject's slug shadows is dead weight that no read would ever
        # reach. Retired subjects count: their slug still resolves, to their redirect.
        self.clean()
        super().save(*args, **kwargs)


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
