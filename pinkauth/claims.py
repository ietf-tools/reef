# Copyright The IETF Trust 2026, All Rights Reserved
"""Shared mapping from OIDC claims to a Pink User.

Used both by the OIDC login backend (interactive login for the Django builder
and analytics site) and by the DRF bearer-token authenticator (API calls
carrying an Authentik access token).
"""

from django.conf import settings
from django.contrib.auth import get_user_model


def is_staff_from_claims(claims) -> bool:
    """Return whether the claims grant staff access, by group membership.

    Staff access is only granted when PINK_OIDC_STAFF_GROUPS is configured and
    the token's groups claim intersects it. Superuser is never granted via
    OIDC; use the local break-glass superuser for that.
    """
    staff_groups = set(getattr(settings, "PINK_OIDC_STAFF_GROUPS", []))
    if not staff_groups:
        return False
    groups_claim = getattr(settings, "PINK_OIDC_GROUPS_CLAIM", "groups")
    user_groups = set(claims.get(groups_claim, []) or [])
    return bool(user_groups & staff_groups)


def sync_user_from_claims(claims):
    """Create or update the User identified by the 'sub' claim.

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
        "is_staff": is_staff_from_claims(claims),
    }

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
