# Copyright The IETF Trust 2026, All Rights Reserved
from django.apps import AppConfig


class ReefAuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "reefauth"

    def ready(self):
        from django.contrib import admin

        from . import checks  # noqa: F401 - registers the system checks

        # Point the admin login page at our template, which adds a link into
        # the reef-admin OIDC flow above the break-glass username/password form.
        admin.site.login_template = "reefauth/admin_login.html"

        admin.site.site_header = "REEF Admin"
        admin.site.site_title = "REEF Admin"
        admin.site.index_title = "REEF Admin"
