# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin
from django.db.models import Count

from reef.admin_documents import DocumentTitleMixin

from .models import Subject, SubjectAlias, SubjectAssignment


class SubjectAssignmentInline(DocumentTitleMixin, admin.TabularInline):
    model = SubjectAssignment
    extra = 0
    # Read-only rather than a column: an inline renders fields, and a title is not
    # one of the model's.
    readonly_fields = ["document_title"]


class SubjectAliasInline(admin.TabularInline):
    """The other names for a subject, curated where the subject is.

    Not an admin of its own: an alias is meaningless apart from what it points at, and
    the question staff have is always "what else should reach this subject" rather
    than "what aliases exist". The list below shows renames arriving here by
    themselves, which is the other reason this is the place to see them.
    """

    model = SubjectAlias
    extra = 0
    fields = ["slug", "created_at"]
    readonly_fields = ["created_at"]


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    """Where the vocabulary is curated, which is the only place it is.

    Nothing self-serves a subject into existence: readers subscribe to
    subjects and Red renders them, but both work from the list made here.
    """

    list_display = [
        "name",
        "slug",
        "assignment_count",
        "alias_list",
        "retired_at",
        "merged_into",
    ]
    list_filter = [("retired_at", admin.EmptyFieldListFilter)]
    actions = ["retire_selected", "unretire_selected"]
    # Typing the name fills the slug, which is the order the two are decided
    # in. It stops filling once the subject has been saved, which is right: a
    # rename leaves the old slug behind as an alias, and growing one of those
    # every time somebody reworded a name would be noise rather than history.
    prepopulated_fields = {"slug": ["name"]}
    # aliases__slug included so that searching the name a reader typed finds the
    # subject it resolves to, which is the question an alias exists to answer.
    search_fields = [
        "name",
        "slug",
        "description",
        "assignments__doc",
        "aliases__slug",
    ]
    inlines = [SubjectAliasInline, SubjectAssignmentInline]

    def get_queryset(self, request):
        # all_objects, because staff have to be able to find a retired subject in
        # order to bring it back or see where it went. Annotated rather than counted
        # per row: the column is on the listing, so counting in the display method
        # would be one query per subject.
        return Subject.all_objects.annotate(
            _assignments=Count("assignments", distinct=True)
        ).prefetch_related("aliases")

    @admin.action(description="Retire selected subjects")
    def retire_selected(self, request, queryset):
        """Stop offering a subject without cutting off the people following it.

        Not a merge: their subscriptions go on matching whatever the subject still
        covers. Use the merge action instead when the followers should end up
        somewhere.
        """
        retired = [subject for subject in queryset if not subject.is_retired]
        for subject in retired:
            subject.retire()
        self.message_user(request, f"Retired {len(retired)} subject(s).")

    @admin.action(description="Bring selected subjects back")
    def unretire_selected(self, request, queryset):
        restored = [subject for subject in queryset if subject.is_retired]
        for subject in restored:
            subject.unretire()
        self.message_user(request, f"Restored {len(restored)} subject(s).")

    @admin.display(description="Documents", ordering="_assignments")
    def assignment_count(self, obj):
        return obj._assignments

    @admin.display(description="Also known as")
    def alias_list(self, obj):
        # Prefetched above, so this is not a query per row.
        return ", ".join(alias.slug for alias in obj.aliases.all())


@admin.register(SubjectAssignment)
class SubjectAssignmentAdmin(DocumentTitleMixin, admin.ModelAdmin):
    """Assignments on their own, for working document-first.

    The inline above is for curating one subject. This is for the other
    direction, where what is in hand is a document and the question is which
    subjects it should carry.
    """

    list_display = ["doc", "document_title", "subject", "assigned_at"]
    list_filter = ["subject"]
    search_fields = ["doc"]
