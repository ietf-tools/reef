# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin

from reef.admin_readonly import ReadOnlyAdminMixin

from .models import ModerationAction


@admin.register(ModerationAction)
class ModerationActionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """The moderator log: what was done, by whom, to what, and why. Read only."""

    list_display = [
        "created_at",
        "action",
        "target_type",
        "target_id",
        "subject_name",
        "actor_name",
    ]
    list_filter = ["action", "target_type"]
    search_fields = ["target_id", "subject_sub", "subject_name", "actor_name", "reason"]
    date_hierarchy = "created_at"
