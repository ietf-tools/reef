# Copyright The IETF Trust 2026, All Rights Reserved
"""Admin plumbing for logged moderation: actions that ask why, and a mixin that
logs a takedown made by editing deleted_at on the change form.
"""

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core.exceptions import ValidationError
from django.db import transaction
from django.template.response import TemplateResponse

from . import review
from .actions import log_action


class ReasonForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        help_text="Kept in the moderator log. Never shown to the user. Optional: "
        "an action that needs doing now should not wait on the wording.",
    )


def reason_action(name, description, prompt, perform):
    """An admin action that asks for a reason before it acts.

    perform(actor, queryset, reason) returns how many rows it changed. It may
    raise ValidationError, which is shown to the moderator and changes nothing.
    """

    @admin.action(description=description)
    def action(modeladmin, request, queryset):
        if request.POST.get("confirm"):
            form = ReasonForm(request.POST)
            if form.is_valid():
                try:
                    with transaction.atomic():
                        done = perform(
                            request.user, queryset, form.cleaned_data["reason"]
                        )
                except ValidationError as exc:
                    modeladmin.message_user(
                        request, " ".join(exc.messages), messages.ERROR
                    )
                else:
                    modeladmin.message_user(
                        request, f"{description}: {done} changed.", messages.SUCCESS
                    )
                return None
        else:
            form = ReasonForm()
        return TemplateResponse(
            request,
            "moderation/confirm.html",
            {
                **modeladmin.admin_site.each_context(request),
                "title": description,
                "prompt": prompt,
                "form": form,
                "queryset": queryset,
                "action_name": name,
                "checkbox_name": ACTION_CHECKBOX_NAME,
                "opts": modeladmin.model._meta,
            },
        )

    action.__name__ = name
    return action


def review_filter(target_type):
    """A changelist filter for what no moderator has labelled yet."""

    class ReviewFilter(admin.SimpleListFilter):
        title = "review"
        parameter_name = "review"

        def lookups(self, request, model_admin):
            return [("unreviewed", "Not yet reviewed"), ("reviewed", "Reviewed")]

        def queryset(self, request, queryset):
            if self.value() not in ("unreviewed", "reviewed"):
                return queryset
            reviewed = review.reviewed_target_ids(target_type)
            if self.value() == "unreviewed":
                return queryset.exclude(pk__in=reviewed)
            return queryset.filter(pk__in=reviewed)

    return ReviewFilter


class LogsRemovalMixin:
    """Logs a change to deleted_at made on the change form.

    The form stays the quick way to take one thing down, and the log still hears
    of it. Set the two actions, the target type, and how to snapshot and attribute
    the object.
    """

    removal_action = None
    restore_action = None
    target_type = None

    def removal_snapshot(self, obj):
        return {}

    def removal_subject(self, obj):
        return None

    def save_model(self, request, obj, form, change):
        before = None
        if change:
            before = (
                type(obj)
                .all_objects.filter(pk=obj.pk)
                .values_list("deleted_at", flat=True)
                .first()
            )
        super().save_model(request, obj, form, change)
        if before is None and obj.deleted_at is not None:
            action = self.removal_action
        elif before is not None and obj.deleted_at is None:
            action = self.restore_action
        else:
            return
        log_action(
            request.user,
            action,
            self.target_type,
            obj.pk,
            subject_user=self.removal_subject(obj),
            reason=obj.deleted_reason,
            snapshot=self.removal_snapshot(obj),
        )
