# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin

from moderation import actions
from moderation.admin_actions import reason_action

from .models import User


def _block(actor, queryset, reason):
    return sum(1 for user in queryset if actions.block_user(actor, user, reason))


def _block_and_remove(actor, queryset, reason):
    return sum(
        1
        for user in queryset
        if actions.block_user(actor, user, reason, remove_content=True)
    )


def _unblock(actor, queryset, reason):
    return sum(1 for user in queryset if actions.unblock_user(actor, user, reason))


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    """Read-mostly: accounts are synced from Authentik claims on login, not typed
    in here. oidc_sub is excluded from edits because it is the binding to that
    identity; changing it by hand detaches the account from the person it
    belongs to. password is excluded because there is no local login: every
    session comes from Authentik.
    """

    list_display = [
        "username",
        "name",
        "email",
        "is_staff",
        "is_superuser",
        "receive_digest_email",
        "blocked_at",
        "last_login",
    ]
    list_filter = [
        "is_staff",
        "is_superuser",
        "is_active",
        "receive_digest_email",
        ("blocked_at", admin.EmptyFieldListFilter),
    ]
    search_fields = ["username", "name", "email", "oidc_sub"]
    filter_horizontal = ["groups", "user_permissions"]
    exclude = ["password"]
    readonly_fields = [
        "oidc_sub",
        "last_login",
        "date_joined",
        "blocked_at",
        "blocked_reason",
        "blocked_by",
    ]
    actions = [
        reason_action(
            "block_accounts",
            "Block selected accounts",
            "These accounts are refused by the API. Their content stays.",
            _block,
        ),
        reason_action(
            "block_accounts_and_remove_content",
            "Block selected accounts and remove their content",
            "These accounts are refused by the API, and their sets and responses "
            "are taken down. Unblocking restores what this removes.",
            _block_and_remove,
        ),
        reason_action(
            "unblock_accounts",
            "Unblock selected accounts",
            "These accounts can use the API again, and what their block removed "
            "is restored. An account disabled in Authentik stays disabled there.",
            _unblock,
        ),
    ]
