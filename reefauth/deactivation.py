# Copyright The IETF Trust 2026, All Rights Reserved
"""Client for the account service's deactivate-user API. No Django imports.

The service decides whether an account may be deactivated and records the reason
and the moderator in its audit log. This only asks, bearing the moderator's own
reef-admin access token.

deactivate raises urllib.error.URLError, HTTPError included, if the service cannot
be reached or refuses, so the caller sees the status code the service gave.
"""

import json
import urllib.request

TIMEOUT_SECONDS = 20


def deactivate(url, token, account_uuid, reason):
    """Ask the service to deactivate the account. Returns the HTTP status."""
    request = urllib.request.Request(
        url,
        data=json.dumps({"uuid": str(account_uuid), "reason": reason}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.status
