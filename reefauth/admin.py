# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin

from .models import User


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
        "last_login",
    ]
    list_filter = ["is_staff", "is_superuser", "is_active", "receive_digest_email"]
    search_fields = ["username", "name", "email", "oidc_sub"]
    filter_horizontal = ["groups", "user_permissions"]
    exclude = ["password"]
    readonly_fields = ["oidc_sub", "last_login", "date_joined"]
