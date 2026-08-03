# Copyright The IETF Trust 2026, All Rights Reserved

# Ensure the Celery app is loaded so shared_task uses it.
from .celery import app as celery_app

__all__ = ("celery_app",)
