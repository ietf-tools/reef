# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin
from django.db.models import Count

from reef.admin_documents import DocumentTitleMixin

from .models import Subject, SubjectAssignment


class SubjectAssignmentInline(DocumentTitleMixin, admin.TabularInline):
    model = SubjectAssignment
    extra = 0
    # Read-only rather than a column: an inline renders fields, and a title is not
    # one of the model's.
    readonly_fields = ["document_title"]


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    """Where the vocabulary is curated, which is the only place it is.

    Nothing self-serves a subject into existence: readers subscribe to
    subjects and Red renders them, but both work from the list made here.
    """

    list_display = ["name", "slug", "assignment_count", "updated_at"]
    # Typing the name fills the slug, which is the order the two are decided
    # in. It stops filling once the subject has been saved, which is right:
    # changing a slug breaks the links naming the old one, so it should take
    # deliberate typing rather than follow a reworded name by itself.
    prepopulated_fields = {"slug": ["name"]}
    search_fields = ["name", "slug", "description", "assignments__doc"]
    inlines = [SubjectAssignmentInline]

    def get_queryset(self, request):
        # Annotated rather than counted per row: the column is on the listing,
        # so counting in the display method would be one query per subject.
        return super().get_queryset(request).annotate(_assignments=Count("assignments"))

    @admin.display(description="Documents", ordering="_assignments")
    def assignment_count(self, obj):
        return obj._assignments


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
