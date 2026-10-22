# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin

from .models import Rating


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin):
    list_display = ["rfc", "user", "value", "updated_at"]
    list_filter = ["value", ("deleted_at", admin.EmptyFieldListFilter)]
    search_fields = ["rfc"]

    def get_queryset(self, request):
        # all_objects, so that staff can find the ratings a block removed.
        return Rating.all_objects.get_queryset()
