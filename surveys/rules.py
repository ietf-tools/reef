# Copyright The IETF Trust 2026, All Rights Reserved
"""Permission rules for the surveys app.

Managing surveys and viewing results are staff-only. Filling out published
surveys and reading the open-survey list are public and handled at the view
level (AllowAny), not here.
"""

import rules


@rules.predicate
def is_staff(user):
    return bool(user and user.is_authenticated and user.is_staff)


rules.add_perm("surveys.manage_surveys", is_staff)
rules.add_perm("surveys.view_results", is_staff)
