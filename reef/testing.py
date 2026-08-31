# Copyright The IETF Trust 2026, All Rights Reserved
"""Test helpers for code that reads Red's document index.

Anything resolving a document identifier goes through reef.rfcmeta, and rfcmeta's
whole job is to fetch from another service. A test that reaches it really does open a
socket to www.rfc-editor.org, which makes the suite slow, dependent on the network,
and dependent on what Red happens to be publishing that day. This has caught two
call sites already; use `stub_rfc_index` at any third.
"""

from unittest import mock

from reef import rfcmeta

# Two documents, each in a subseries, which is enough for the cases that matter:
# resolving a title, expanding a subseries, and missing entirely.
DEFAULT_MAPPING = {
    "rfc9110": {"title": "HTTP Semantics", "subseries": ["std97"]},
    "rfc2119": {"title": "Key words", "subseries": ["bcp14"]},
}


def stub_rfc_index(test, mapping=None):
    """Warm rfcmeta's process memo, so nothing in this test reaches Red.

    Registers cleanup on `test`, which matters more than it looks: the memo is module
    state, so a test leaving it warm would leak an index into whatever ran next.
    """
    rfcmeta.clear_cache()
    test.addCleanup(rfcmeta.clear_cache)

    rfcmeta._memo["value"] = (
        DEFAULT_MAPPING if mapping is None else mapping,
        None,
    )
    rfcmeta._memo["expires"] = float("inf")

    # Belt and braces: if something bypasses the memo, it fails loudly here rather
    # than quietly making a request.
    patcher = mock.patch(
        "urllib.request.urlopen",
        side_effect=AssertionError("a test tried to fetch from Red"),
    )
    patcher.start()
    test.addCleanup(patcher.stop)
