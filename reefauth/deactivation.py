# Copyright The IETF Trust 2026, All Rights Reserved
"""Client for the account service's deactivate-user API. No Django imports.

The service decides whether an account may be deactivated and records the reason
in its audit log. This only asks. It authenticates as Reef's own service client
(client credentials), because the moderator's session token is not available to
everything that may need it and expires.

Both calls raise urllib.error.URLError, HTTPError included, if the service cannot
be reached or refuses, so the caller sees the status code the service gave.
"""

import json
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 20


def fetch_token(token_endpoint, client_id, client_secret):
    """A fresh access token for the service client.

    Fetched for each deactivation rather than kept: they are rare and moderators
    wait on them, and a kept token is one more thing that can expire unseen.
    """
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode()
    request = urllib.request.Request(
        token_endpoint,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        payload = json.load(response)
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise ValueError("The token endpoint gave no access_token")
    return token


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
