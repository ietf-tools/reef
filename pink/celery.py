# Copyright The IETF Trust 2026, All Rights Reserved
"""Celery application for the Pink project."""

import os

from celery import Celery
from celery import signals as celery_signals


# Disable Celery's internal logging configuration; it is set up via Django.
@celery_signals.setup_logging.connect
def on_setup_logging(**kwargs):
    pass


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pink.settings")

app = Celery("pink")

# Configuration keys are read from Django settings with a CELERY_ prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

app.conf.timezone = "UTC"
