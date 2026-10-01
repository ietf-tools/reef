# Copyright The IETF Trust 2026, All Rights Reserved
"""An admin page that shows rows and refuses to add, change or delete them.

For tables that some task or upload writes, where a row edited by hand would be
overwritten or would break what the writer relies on. Each user says which.
"""


class ReadOnlyAdminMixin:
    """Mixed in ahead of admin.ModelAdmin."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
