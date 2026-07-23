# Copyright The IETF Trust 2026, All Rights Reserved
"""Django settings selector.

The active settings module is chosen by the PINK_DEPLOYMENT_MODE environment
variable: development, staging, production (default), or build.
"""

import os

DEPLOYMENT_MODE = os.environ.get("PINK_DEPLOYMENT_MODE", "production")

if DEPLOYMENT_MODE == "development":
    from .development import *
elif DEPLOYMENT_MODE == "staging":
    from .staging import *
elif DEPLOYMENT_MODE == "build":
    from .build import *
else:
    from .production import *
