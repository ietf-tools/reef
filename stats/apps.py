# Copyright The IETF Trust 2026, All Rights Reserved
from django.apps import AppConfig


class StatsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "stats"
    verbose_name = "Per-document statistics"
