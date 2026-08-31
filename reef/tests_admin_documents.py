# Copyright The IETF Trust 2026, All Rights Reserved
"""The title column the admin shows beside a bare document identifier."""

from django.contrib import admin
from django.test import SimpleTestCase
from django.utils.html import strip_tags

from reef import rfcmeta
from reef.admin_documents import DocumentTitleMixin
from reef.testing import warm_rfc_index


class Row:
    """Stands in for a model instance; the mixin only reads one attribute."""

    def __init__(self, doc):
        self.doc = doc


class FakeAdmin(DocumentTitleMixin, admin.ModelAdmin):
    def __init__(self):  # no model or site needed to render one column
        pass


class DocumentTitleTests(SimpleTestCase):
    def setUp(self):
        rfcmeta.clear_cache()
        self.addCleanup(rfcmeta.clear_cache)
        self.admin = FakeAdmin()

    def warm(self, mapping):
        warm_rfc_index(mapping, None)

    def test_a_resolved_document_shows_its_title(self):
        self.warm({"rfc9110": {"title": "HTTP Semantics", "subseries": []}})
        self.assertEqual(self.admin.document_title(Row("rfc9110")), "HTTP Semantics")

    def test_the_identifier_is_canonicalised_before_lookup(self):
        """Staff type what they read, and the admin stores what it is given."""
        self.warm({"rfc9110": {"title": "HTTP Semantics", "subseries": []}})
        self.assertEqual(self.admin.document_title(Row("RFC 9110")), "HTTP Semantics")

    def test_a_document_the_index_lacks_is_called_out(self):
        """A curation error, and the only place Reef would notice one."""
        self.warm({"rfc9110": {"title": "HTTP Semantics", "subseries": []}})
        rendered = strip_tags(self.admin.document_title(Row("rfc99999")))
        self.assertEqual(rendered, "unknown document")

    def test_a_cold_index_shows_nothing_rather_than_unknown(self):
        """The distinction that matters: with no index loaded, every row would
        otherwise read as a curation error."""
        self.assertEqual(self.admin.document_title(Row("rfc9110")), "")

    def test_rendering_a_row_never_reaches_red(self):
        from unittest import mock

        with mock.patch("urllib.request.urlopen") as urlopen:
            self.admin.document_title(Row("rfc9110"))
        urlopen.assert_not_called()

    def test_an_unparseable_identifier_is_called_out_not_crashed_on(self):
        self.warm({"rfc9110": {"title": "HTTP Semantics", "subseries": []}})
        rendered = strip_tags(self.admin.document_title(Row("nonsense")))
        self.assertEqual(rendered, "unknown document")

    def test_a_blank_identifier_renders_blank(self):
        self.warm({})
        self.assertEqual(self.admin.document_title(Row("")), "")
