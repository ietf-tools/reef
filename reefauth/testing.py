# Copyright The IETF Trust 2026, All Rights Reserved
"""Test helpers for signing a user into the admin as Authentik would."""

import time

# SessionRefresh sends a GET back through Authentik once this has passed; an hour
# outlasts any test.
_TOKEN_LIFETIME_SECONDS = 60 * 60


def login(client, user):
    """Log `user` into `client` with a session SessionRefresh accepts.

    force_login alone marks the session as an OIDC login but leaves out the id token
    expiry a real reef-admin login stores, so SessionRefresh treats the token as
    already expired and redirects every GET to Authentik.
    """
    client.force_login(user)
    session = client.session
    session["oidc_id_token_expiration"] = time.time() + _TOKEN_LIFETIME_SECONDS
    session.save()
