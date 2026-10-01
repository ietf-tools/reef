# Copyright The IETF Trust 2026, All Rights Reserved
from django.apps import AppConfig
from django.conf import settings


class ReefAuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "reefauth"

    def ready(self):
        from django.contrib import admin

        from . import checks  # noqa: F401 - registers the system checks

        # Point the admin login page at our template, which sends staff into the
        # reef-admin OIDC flow, with Django's username/password form only when
        # REEF_ADMIN_PASSWORD_LOGIN is on (development only).
        admin.site.login_template = "reefauth/admin_login.html"
        admin.site.login = _flag_password_login(admin.site.login)

        admin.site.site_header = "REEF Admin"
        admin.site.site_title = "REEF Admin"
        admin.site.index_title = "REEF Admin"


def _flag_password_login(login):
    """Tell the login template whether to show Django's password form as well."""

    def wrapper(request, extra_context=None):
        password_login = getattr(settings, "REEF_ADMIN_PASSWORD_LOGIN", False)
        return login(
            request, {**(extra_context or {}), "password_login": password_login}
        )

    return wrapper
