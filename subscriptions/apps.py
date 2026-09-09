# Copyright The IETF Trust 2026, All Rights Reserved
from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "subscriptions"

    def ready(self):
        from . import signals  # noqa: F401 - registers the new-assignment receiver
