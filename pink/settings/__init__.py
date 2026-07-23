# Copyright The IETF Trust 2026, All Rights Reserved
"""Django settings selector.

The active settings module is chosen by the PINK_DEPLOYMENT_MODE environment
variable. Only the development module exists at this stage; the staging,
production, and build modules are added alongside the deployment work.
"""

import os

DEPLOYMENT_MODE = os.environ.get("PINK_DEPLOYMENT_MODE", "development")

# Only the development module exists at this stage. The staging, production,
# and build modules are added with the deployment work, at which point this
# selector branches on DEPLOYMENT_MODE.
from .development import *  # noqa: E402,F401,F403
