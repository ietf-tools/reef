# Copyright The IETF Trust 2026, All Rights Reserved
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from reef.docids import display_doc_id, normalize_doc_id


class NormalizeDocIdTests(SimpleTestCase):
    def test_canonical_form(self):
        cases = {
            "rfc9110": "rfc9110",
            "RFC9110": "rfc9110",
            "RFC 9110": "rfc9110",
            "rfc-9110": "rfc9110",
            "rfc_9110": "rfc9110",
            " rfc0791 ": "rfc791",
            "BCP 14": "bcp14",
            "std66": "std66",
            "FYI-0036": "fyi36",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_doc_id(value), expected)

    def test_bare_number_needs_a_series(self):
        with self.assertRaises(ValidationError):
            normalize_doc_id("9110")

    def test_bare_number_reads_as_the_default_series(self):
        self.assertEqual(normalize_doc_id("9110", default_series="rfc"), "rfc9110")
        self.assertEqual(normalize_doc_id("0791", default_series="rfc"), "rfc791")

    def test_an_explicit_series_beats_the_default(self):
        self.assertEqual(normalize_doc_id("bcp14", default_series="rfc"), "bcp14")

    def test_rejected(self):
        for value in (
            "",
            "   ",
            None,
            9110,
            "draft-ietf-httpbis-semantics",
            "rfc",
            "rfc9110bis",
            "internet-draft-1",
            "rfc" + "9" * 31,  # over DOC_ID_MAX_LENGTH
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    normalize_doc_id(value, default_series="rfc")


class DisplayDocIdTests(SimpleTestCase):
    def test_prose_form(self):
        cases = {
            "rfc9110": "RFC 9110",
            "bcp14": "BCP 14",
            "std66": "STD 66",
            "fyi36": "FYI 36",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(display_doc_id(value), expected)

    def test_anything_unparsed_comes_back_as_given(self):
        # A caller rendering a value from the datatracker feed shows what it
        # was given rather than dropping it.
        for value in ("", None, "RFC 9110", "draft-ietf-httpbis-semantics"):
            with self.subTest(value=value):
                self.assertEqual(display_doc_id(value), value)
