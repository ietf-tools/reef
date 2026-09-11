# Copyright The IETF Trust 2026, All Rights Reserved
from django.apps import AppConfig


class ReefAuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "reefauth"

    def ready(self):
        from . import checks  # noqa: F401 - registers the system checks
