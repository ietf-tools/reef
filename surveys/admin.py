# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin

from moderation import actions, review
from moderation.admin_actions import LogsRemovalMixin, reason_action, review_filter
from moderation.models import ModerationAction, ModerationFlag

from .models import Response, Survey


def _withdraw(actor, queryset, reason):
    return sum(
        1 for obj in queryset if actions.withdraw_survey(actor, obj, reason) is not None
    )


def _restore_surveys(actor, queryset, reason):
    return sum(
        1 for obj in queryset if actions.restore_survey(actor, obj, reason) is not None
    )


def _delete_responses(actor, queryset, reason):
    return len(actions.delete_responses(actor, queryset, reason))


def _spam(actor, queryset, reason):
    return review.review_responses(actor, queryset, ModerationFlag.Verdict.SPAM, reason)


def _fine(actor, queryset, reason):
    return review.review_responses(actor, queryset, ModerationFlag.Verdict.FINE, reason)


def _restore_responses(actor, queryset, reason):
    return len(actions.restore_responses(actor, queryset, reason))


@admin.register(Survey)
class SurveyAdmin(LogsRemovalMixin, admin.ModelAdmin):
    removal_action = ModerationAction.Action.WITHDRAW_SURVEY
    restore_action = ModerationAction.Action.RESTORE_SURVEY
    target_type = "survey"
    removal_snapshot = staticmethod(actions.snapshot_survey)

    def removal_subject(self, obj):
        return obj.created_by

    actions = [
        reason_action(
            "withdraw_surveys",
            "Withdraw selected surveys",
            "These surveys will 404 for the runner and the builder alike.",
            _withdraw,
        ),
        reason_action(
            "restore_surveys",
            "Restore selected surveys",
            "These surveys will be available again.",
            _restore_surveys,
        ),
    ]

    list_display = ["slug", "title", "status", "visibility", "deleted_at", "updated_at"]
    list_filter = ["status", "visibility", ("deleted_at", admin.EmptyFieldListFilter)]
    search_fields = ["slug", "title"]
    readonly_fields = ["created_at", "updated_at"]

    def get_queryset(self, request):
        # all_objects, not the default manager: staff have to be able to find a
        # survey they withdrew, both to read its responses and to undo it.
        return Survey.all_objects.get_queryset()


@admin.register(Response)
class ResponseAdmin(admin.ModelAdmin):
    """Responses are removed through the actions, which log it, never deleted.

    Neither the row's own delete nor the bulk delete is offered: both would
    destroy the answers with nothing in the moderator log.
    """

    list_display = [
        "survey",
        "submitted_by",
        "submitted_at",
        "duration_seconds",
        "deleted_at",
    ]
    list_filter = [
        "survey",
        ("deleted_at", admin.EmptyFieldListFilter),
        review_filter("response"),
    ]
    readonly_fields = [
        "survey",
        "data",
        "submitted_by",
        "submitted_at",
        "meta",
        "duration_seconds",
        "pages_viewed",
        "deleted_at",
        "deleted_reason",
    ]
    actions = [
        reason_action(
            "mark_responses_spam",
            "Mark selected responses as spam",
            "These responses will be removed and kept as examples of spam.",
            _spam,
        ),
        reason_action(
            "mark_responses_fine",
            "Mark selected responses as fine",
            "These responses will be kept as examples of content that is not spam.",
            _fine,
        ),
        reason_action(
            "delete_responses",
            "Delete selected responses",
            "These responses will be left out of analytics, results and exports.",
            _delete_responses,
        ),
        reason_action(
            "restore_responses",
            "Restore selected responses",
            "These responses will count again.",
            _restore_responses,
        ),
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return Response.all_objects.get_queryset()
