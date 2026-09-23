# Copyright The IETF Trust 2026, All Rights Reserved
"""Shared mapping from OIDC claims to a Reef User.

Used both by the OIDC login backend (interactive login for the Django builder
and analytics site) and by the DRF bearer-token authenticator (API calls
carrying an Authentik access token). Only the login backend decides
is_staff and is_superuser; see sync_user_from_claims().
"""

from django.conf import settings
from django.contrib.auth import get_user_model


def is_staff_from_claims(claims) -> bool:
    """Return whether the claims grant staff access, by group membership.

    Staff access is granted when REEF_OIDC_STAFF_GROUPS is configured and the
    token's groups claim intersects it, or when is_superuser_from_claims()
    does — the admin site refuses a superuser who isn't also staff.
    """
    staff_groups = set(getattr(settings, "REEF_OIDC_STAFF_GROUPS", []))
    groups_claim = getattr(settings, "REEF_OIDC_GROUPS_CLAIM", "groups")
    user_groups = set(claims.get(groups_claim, []) or [])
    return bool(user_groups & staff_groups) or is_superuser_from_claims(claims)


def is_superuser_from_claims(claims) -> bool:
    """Return whether the claims grant superuser access, by group membership.

    Only granted when REEF_OIDC_SUPERUSER_GROUPS is configured and the token's
    groups claim intersects it. Empty by default: the local break-glass
    superuser remains the only one until an operator opts a group in.
    """
    superuser_groups = set(getattr(settings, "REEF_OIDC_SUPERUSER_GROUPS", []))
    if not superuser_groups:
        return False
    groups_claim = getattr(settings, "REEF_OIDC_GROUPS_CLAIM", "groups")
    user_groups = set(claims.get(groups_claim, []) or [])
    return bool(user_groups & superuser_groups)


def sync_user_from_claims(claims, *, sync_permissions=False):
    """Create or update the User identified by the 'sub' claim.

    is_staff and is_superuser are written only when sync_permissions is set,
    which only the reef-admin login does. Bearer tokens come from other
    Authentik applications (the survey runner, Red) whose claims need not carry
    the groups claim, and every login shares one User row per subject, so
    letting them write these fields would demote a signed-in admin on their
    next API call. A User first created from a bearer token starts with neither.

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
    if sync_permissions:
        desired["is_staff"] = is_staff_from_claims(claims)
        desired["is_superuser"] = is_superuser_from_claims(claims)

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
