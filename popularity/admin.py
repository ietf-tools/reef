# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin

from .models import PopularEntry


@admin.register(PopularEntry)
class PopularEntryAdmin(admin.ModelAdmin):
    list_display = ["rank", "rfc"]
    list_editable = ["rfc"]
    ordering = ["rank"]
