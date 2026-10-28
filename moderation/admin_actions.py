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

from . import actions, review
from .actions import Deactivation, log_action

# Where mozilla-django-oidc keeps the access token, given OIDC_STORE_ACCESS_TOKEN.
ACCESS_TOKEN_KEY = "oidc_access_token"


class ReasonForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        help_text="Kept in the moderator log. Never shown to the user. Optional: "
        "an action that needs doing now should not wait on the wording.",
    )

    def __init__(self, *args, required=False, **kwargs):
        super().__init__(*args, **kwargs)
        if required:
            field = self.fields["reason"]
            field.required = True
            field.initial = actions.DEFAULT_REASON
            field.help_text = (
                "Kept in the moderator log and sent to the account service, which "
                "records it. Never shown to the user."
            )


def reason_action(
    name,
    description,
    prompt,
    perform,
    *,
    reason_required=False,
    needs_request=False,
):
    """An admin action that asks for a reason before it acts.

    perform(actor, queryset, reason) returns how many rows it changed. It may
    raise ValidationError, which is shown to the moderator and changes nothing.

    With needs_request, perform gets the request instead of the actor, to tell the
    moderator how something went, and is not wrapped in a transaction: it may call
    out to another service, and a rollback must not erase the record of what that
    service did.
    """

    @admin.action(description=description)
    def action(modeladmin, request, queryset):
        if request.POST.get("confirm"):
            form = ReasonForm(request.POST, required=reason_required)
            if form.is_valid():
                reason = form.cleaned_data["reason"]
                try:
                    if needs_request:
                        done = perform(request, queryset, reason)
                    else:
                        with transaction.atomic():
                            done = perform(request.user, queryset, reason)
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
            form = ReasonForm(required=reason_required)
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


def report_deactivation(request, user, outcome):
    """Tell the moderator how the account service answered, in plain words."""
    who = str(user)
    if outcome.status == Deactivation.DEACTIVATED:
        said = f" It said: {outcome.detail}" if outcome.detail else ""
        messages.success(
            request,
            f"{who} was deactivated in the account service "
            f"(HTTP {outcome.http_status}).{said}",
        )
    elif outcome.status == Deactivation.REFUSED:
        said = f": {outcome.detail}" if outcome.detail else ""
        messages.warning(
            request,
            f"The account service refused to deactivate {who} "
            f"(HTTP {outcome.http_status}){said}. They are blocked in Reef only.",
        )
        if outcome.http_status == 401:
            messages.info(
                request,
                "A 401 usually means your token has expired: sign out and in "
                "again, then deactivate them from the moderation page.",
            )
    elif outcome.status == Deactivation.UNREACHABLE:
        messages.error(
            request,
            f"The account service could not be reached to deactivate {who} "
            f"({outcome.detail}). They are blocked in Reef only; deactivate them "
            "again from the moderation page.",
        )
    elif outcome.status == Deactivation.NO_TOKEN:
        messages.warning(
            request,
            f"Your session holds no account service token, so {who} was not "
            "deactivated there. They are blocked in Reef only. Sign out and in "
            "again, then deactivate them from the moderation page.",
        )
    else:
        messages.warning(
            request,
            f"{who} has no account id on file yet, so the account service was not "
            "asked. They are blocked in Reef only; the id is recorded the next "
            "time they use a token that carries it, and then they can be "
            "deactivated from the moderation page.",
        )


def block_accounts(request, users, reason, *, remove_content):
    """Block each of users, ask the account service to deactivate them, and tell the
    moderator how each went. Returns how many were newly blocked.

    Everything that can be refused is checked before anything is done, so a refused
    block changes nothing. Each account is then its own step: the service has acted
    by the time it answers, so a later failure must not undo the log of an earlier
    one.
    """
    users = list(users)
    for user in users:
        actions.validate_blockable(request.user, user)
    blocked = 0
    for user in users:
        block, outcome = actions.block_and_deactivate(
            request.user,
            user,
            reason,
            token=request.session.get(ACCESS_TOKEN_KEY),
            remove_content=remove_content,
        )
        if block is None:
            continue
        blocked += 1
        if outcome is not None:
            report_deactivation(request, user, outcome)
    if blocked and not actions.deactivation_enabled():
        messages.info(
            request,
            "Deactivation in the account service is not configured, so the "
            "accounts are blocked in Reef only.",
        )
    return blocked


def deactivate_accounts(request, users, reason):
    """Ask the account service again about accounts already blocked here."""
    if not actions.deactivation_enabled():
        messages.error(
            request, "Deactivation in the account service is not configured."
        )
        return 0
    asked = 0
    for user in users:
        if not user.is_blocked:
            continue
        outcome = actions.deactivate_account(
            request.user,
            user,
            reason or user.blocked_reason,
            token=request.session.get(ACCESS_TOKEN_KEY),
        )
        report_deactivation(request, user, outcome)
        asked += 1
    return asked


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
