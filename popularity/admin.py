# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin

from reef.admin_documents import DocumentTitleMixin

from .models import PopularEntry


@admin.register(PopularEntry)
class PopularEntryAdmin(DocumentTitleMixin, admin.ModelAdmin):
    document_field = "rfc"

    list_display = ["rank", "rfc", "document_title"]
    list_editable = ["rfc"]
    ordering = ["rank"]
