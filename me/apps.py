# Copyright The IETF Trust 2026, All Rights Reserved
from django.apps import AppConfig


class MeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "me"
    verbose_name = "The caller's own engagement"
