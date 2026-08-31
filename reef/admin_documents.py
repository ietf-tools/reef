# Copyright The IETF Trust 2026, All Rights Reserved
"""A document's title, beside its identifier, in the admin.

Reef stores identifiers and nothing else about a document, which is right for the
database and unhelpful for a person: curating subjects across nine thousand documents
means reading rfc9110 and knowing what it is. The title comes from Red at display
time through reef.rfcmeta, so nothing is stored and nothing can go stale in a column.

Resolution is per row but the index behind it is shared and memoised per process, so
a hundred-row changelist costs one dictionary lookup each. Nothing here ever fetches:
a page render must not wait on a 6.8 MB download, so it uses the index if the
precomputer has warmed it and shows no title if not.
"""

from django.contrib import admin
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.html import format_html

from reef import rfcmeta
from reef.docids import normalize_doc_id


class DocumentTitleMixin:
    """Adds a `document_title` display column to a ModelAdmin.

    Set `document_field` to the attribute holding the identifier. Add
    "document_title" to list_display, and to readonly_fields if you want it on the
    change form too.
    """

    document_field = "doc"

    @admin.display(description="Title")
    def document_title(self, obj):
        doc = getattr(obj, self.document_field, None)
        if not doc:
            return ""

        mapping = rfcmeta.cached_mapping()
        if mapping is None:
            # Nobody has loaded the index yet. Say nothing rather than something
            # wrong: reporting every row as an unknown document would turn a cold
            # cache into what looks like a catalogue of curation errors.
            return ""

        try:
            meta = mapping.get(normalize_doc_id(doc))
        except DjangoValidationError:
            meta = None
        if meta:
            return meta["title"]

        # Called out rather than blank, because an identifier naming no published
        # document is a curation error and this is the only place Reef would notice
        # one. Reachable only when the index did load, so it means what it says.
        return format_html('<span style="color:#999">unknown document</span>')
