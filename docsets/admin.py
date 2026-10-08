# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin
from django.core.exceptions import ValidationError

from reef.admin_documents import DocumentTitleMixin
from reef.docids import normalize_doc_id

from .models import DocumentSet, DocumentSetEntry


class DocumentSetEntryInline(DocumentTitleMixin, admin.TabularInline):
    model = DocumentSetEntry
    extra = 0
    readonly_fields = ["document_title"]


@admin.register(DocumentSet)
class DocumentSetAdmin(admin.ModelAdmin):
    """Where a set is taken down, and the only place a taken-down one shows.

    Fill in deleted_at, with a reason if there is one to give, and the set 404s
    everywhere: for anonymous readers, for the API, and for its owner. Clearing
    deleted_at restores it, along with its documents and its subscriptions.
    """

    list_display = ["title", "owner", "deleted_at", "updated_at"]
    list_filter = [("deleted_at", admin.EmptyFieldListFilter)]
    search_fields = ["id", "title", "description"]
    fields = ["id", "owner", "title", "description", "deleted_at", "deleted_reason"]
    readonly_fields = ["id"]
    inlines = [DocumentSetEntryInline]

    def get_search_results(self, request, queryset, search_term):
        """Also match the term as a document identifier, so "RFC 9110" and "9110"
        find the sets holding rfc9110. Matched exactly: a substring search on
        documents would also find rfc19110."""
        unsearched = queryset
        queryset, may_have_duplicates = super().get_search_results(
            request, queryset, search_term
        )
        try:
            doc = normalize_doc_id(search_term, default_series="rfc")
        except ValidationError:
            return queryset, may_have_duplicates
        return queryset | unsearched.filter(entries__doc=doc), True

    def get_queryset(self, request):
        # all_objects, not the default manager: staff have to be able to find a
        # set they took down, both to review the decision and to undo it.
        return DocumentSet.all_objects.get_queryset()


@admin.register(DocumentSetEntry)
class DocumentSetEntryAdmin(admin.ModelAdmin):
    list_display = ["document_set", "doc", "rank"]
    search_fields = ["doc"]
