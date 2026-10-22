# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin
from django.core.exceptions import ValidationError

from moderation import actions
from moderation.admin_actions import LogsRemovalMixin, reason_action
from moderation.models import ModerationAction
from reef.admin_documents import DocumentTitleMixin
from reef.docids import normalize_doc_id

from .models import DocumentSet, DocumentSetEntry


class DocumentSetEntryInline(DocumentTitleMixin, admin.TabularInline):
    model = DocumentSetEntry
    extra = 0
    readonly_fields = ["document_title"]


def _take_down(actor, queryset, reason):
    return sum(
        1 for obj in queryset if actions.take_down_set(actor, obj, reason) is not None
    )


def _restore(actor, queryset, reason):
    return sum(
        1 for obj in queryset if actions.restore_set(actor, obj, reason) is not None
    )


@admin.register(DocumentSet)
class DocumentSetAdmin(LogsRemovalMixin, admin.ModelAdmin):
    """Where a set is taken down, and the only place a taken-down one shows.

    Fill in deleted_at, with a reason if there is one to give, and the set 404s
    everywhere: for anonymous readers, for the API, and for its owner. Clearing
    deleted_at restores it, along with its documents and its subscriptions.
    The actions do the same for several sets at once. Either way it is recorded
    in the moderator log.
    """

    removal_action = ModerationAction.Action.TAKE_DOWN_SET
    restore_action = ModerationAction.Action.RESTORE_SET
    target_type = "documentset"
    removal_snapshot = staticmethod(actions.snapshot_set)

    def removal_subject(self, obj):
        return obj.owner

    actions = [
        reason_action(
            "take_down_sets",
            "Take down selected sets",
            "These sets will 404 everywhere, for their owners too.",
            _take_down,
        ),
        reason_action(
            "restore_sets",
            "Restore selected sets",
            "These sets will be readable again.",
            _restore,
        ),
    ]

    list_display = ["title", "owner", "deleted_at", "updated_at"]
    list_filter = [("deleted_at", admin.EmptyFieldListFilter)]
    search_fields = ["id", "title", "description"]
    fields = ["id", "owner", "title", "description", "deleted_at", "deleted_reason"]
    readonly_fields = ["id"]
    inlines = [DocumentSetEntryInline]

    def get_search_results(self, request, queryset, search_term):
        """Also match the term as a document identifier, so "RFC 9110" and "9110"
        find the sets holding rfc9110."""
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
