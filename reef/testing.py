# Copyright The IETF Trust 2026, All Rights Reserved
"""Test helpers for code that reads Red's document index.

Anything resolving a document identifier goes through reef.rfcmeta, and rfcmeta's
whole job is to fetch from another service. A test that reaches it really does open a
socket to www.rfc-editor.org, which makes the suite slow, dependent on the network,
and dependent on what Red happens to be publishing that day. Three call sites did it
without anybody noticing, which is why reef.test_runner now refuses urlopen for the
whole suite: this is how a test says it meant to resolve documents.
"""

from unittest import mock

from reef import rfcmeta

# Two documents, each in a subseries, which is enough for the cases that matter:
# resolving a title, expanding a subseries, and missing entirely.
DEFAULT_MAPPING = {
    "rfc9110": {"title": "HTTP Semantics", "subseries": ["std97"]},
    "rfc2119": {"title": "Key words", "subseries": ["bcp14"]},
}


def document_meta(**overrides):
    """One document as rfcmeta reduces it, for a test that needs a whole entry.

    Here rather than in each test module because the shape belongs to rfcmeta: when a
    field is added to the reduction, this is the one place that has to learn about it.
    """
    return {
        "title": "HTTP Semantics",
        "subseries": [],
        "status": "ps",
        "status_name": "proposed standard",
        "obsoleted_by": [],
        "updates": [],
        "updated_by": [],
        **overrides,
    }


def warm_rfc_index(mapping, created_on=None):
    """Put a mapping in front of rfcmeta, replacing whatever was there.

    For a test that has already stubbed the index and needs it to say something else
    partway through -- Red having republished between two runs, most often. Reaching
    into rfcmeta's memo is what every caller was doing by hand; doing it here means
    only one place knows the attribute's name.
    """
    rfcmeta._memo["value"] = (mapping, created_on)
    rfcmeta._memo["expires"] = float("inf")


def stub_rfc_index(test, mapping=None):
    """Warm rfcmeta's process memo, so nothing in this test reaches Red.

    Registers cleanup on `test`, which matters more than it looks: the memo is module
    state, so a test leaving it warm would leak an index into whatever ran next.
    """
    rfcmeta.clear_cache()
    test.addCleanup(rfcmeta.clear_cache)

    warm_rfc_index(DEFAULT_MAPPING if mapping is None else mapping)

    # Belt and braces: if something bypasses the memo, it fails loudly here rather
    # than quietly making a request.
    patcher = mock.patch(
        "urllib.request.urlopen",
        side_effect=AssertionError("a test tried to fetch from Red"),
    )
    patcher.start()
    test.addCleanup(patcher.stop)
