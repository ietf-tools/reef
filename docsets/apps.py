# Copyright The IETF Trust 2026, All Rights Reserved
from django.apps import AppConfig


class DocsetsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "docsets"
    verbose_name = "Document sets"
