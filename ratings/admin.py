# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin

from .models import Rating


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin):
    list_display = ["rfc", "user", "value", "updated_at"]
    list_filter = ["value"]
    search_fields = ["rfc"]
