# Copyright The IETF Trust 2026, All Rights Reserved
"""Staging-mode Django settings for the Reef project.

Staging uses the production settings; environment values (allowed hosts,
database, OIDC redirect URIs) differ per deployment.
"""

from .production import *
