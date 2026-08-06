# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin

from .models import DocumentSet, DocumentSetEntry


class DocumentSetEntryInline(admin.TabularInline):
    model = DocumentSetEntry
    extra = 0


@admin.register(DocumentSet)
class DocumentSetAdmin(admin.ModelAdmin):
    list_display = ["title", "owner", "visibility", "slug", "updated_at"]
    list_filter = ["visibility"]
    search_fields = ["title", "description", "entries__doc"]
    inlines = [DocumentSetEntryInline]


@admin.register(DocumentSetEntry)
class DocumentSetEntryAdmin(admin.ModelAdmin):
    list_display = ["document_set", "doc", "rank"]
    search_fields = ["doc"]
