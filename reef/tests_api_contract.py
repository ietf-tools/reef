# Copyright The IETF Trust 2026, All Rights Reserved
"""The committed API contract has to match the code that generates it.

reef_api.yaml is not documentation, it is the contract Red and the Nuxt client
generate their types from, and it is committed rather than built. So it goes stale
silently: a serializer changes, the file does not, and the next consumer to regenerate
gets types describing an API that no longer exists. That has happened once already,
and it took the subjects redirect shape with it.

CI runs `spectacular --validate`, which checks the schema is well formed, not that
the committed one is current -- exactly the gap this closes. Red's precomputer has
the same guard for its generated JSON Schema, for the same reason.
"""

import difflib
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase

CONTRACT = Path(__file__).resolve().parent.parent / "reef_api.yaml"


class ApiContractTests(SimpleTestCase):
    maxDiff = None

    def test_the_committed_contract_matches_the_code(self):
        generated = StringIO()
        call_command("spectacular", stdout=generated)

        current = generated.getvalue().strip().splitlines()
        committed = CONTRACT.read_text().strip().splitlines()
        if current == committed:
            return

        # A unified diff rather than assertEqual's, which on two thousand-line
        # documents reports how many characters differ and then declines to show
        # them. What somebody needs here is the handful of lines that moved.
        changed = list(
            difflib.unified_diff(
                committed, current, "reef_api.yaml", "generated", lineterm="", n=1
            )
        )
        self.fail(
            "reef_api.yaml is out of date. Regenerate it with\n"
            "    REEF_DEPLOYMENT_MODE=build ./manage.py spectacular "
            "--file reef_api.yaml --validate\n"
            "and commit the result, so that whatever generates types from it "
            "describes the API this code actually serves.\n\n" + "\n".join(changed[:60])
        )
