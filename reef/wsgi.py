# Copyright The IETF Trust 2026, All Rights Reserved
"""WSGI config for the Reef project.

It exposes the WSGI callable as a module-level variable named ``application``.
See https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "reef.settings")

application = get_wsgi_application()
