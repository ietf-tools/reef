# Copyright The IETF Trust 2026, All Rights Reserved
"""Shared mapping from OIDC claims to a Reef User.

Used both by the OIDC login backend (interactive login for the Django builder
and analytics site) and by the DRF bearer-token authenticator (API calls
carrying an Authentik access token). Only the login backend grants
is_staff and is_superuser; see sync_user_from_claims().
"""

import uuid

from django.contrib.auth import get_user_model


def sync_user_from_claims(claims, *, sync_permissions=False):
    """Create or update the User identified by the 'sub' claim.

    A valid reef-admin login is trusted: sync_permissions, which only that login
    sets, makes the user staff and superuser. Bearer tokens come from other
    Authentik applications (the survey runner, Red), so they never grant either,
    and they never revoke it: every login shares one User row per subject, and a
    token write would demote a signed-in admin on their next API call.

    Returns the User, or None if the claims carry no subject.
    """
    sub = claims.get("sub")
    if not sub:
        return None

    user_model = get_user_model()
    desired = {
        "username": f"authentik-{sub}",
        "name": claims.get("name") or claims.get("preferred_username") or "",
        "email": claims.get("email") or "",
        "avatar": claims.get("picture") or "",
    }
    # Only ever set, never cleared: a token from an application that does not
    # carry the claim must not erase what another one supplied.
    try:
        desired["account_uuid"] = uuid.UUID(str(claims.get("account_uuid")))
    except ValueError:
        pass
    if sync_permissions:
        desired["is_staff"] = True
        desired["is_superuser"] = True

    user, created = user_model.objects.get_or_create(
        oidc_sub=sub,
        defaults=desired,
    )
    if not created:
        changed = False
        for field, value in desired.items():
            if field == "username":
                continue  # never rewrite the stable username
            if getattr(user, field) != value:
                setattr(user, field, value)
                changed = True
        if changed:
            user.save()
    return user
