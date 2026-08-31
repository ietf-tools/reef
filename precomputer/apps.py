# Copyright The IETF Trust 2026, All Rights Reserved
from django.apps import AppConfig


class PrecomputerConfig(AppConfig):
    name = "precomputer"

    def ready(self):
        from . import signals  # noqa: F401 - registers the curated-change receivers
