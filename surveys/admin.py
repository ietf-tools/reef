# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin

from .models import Response, Survey


@admin.register(Survey)
class SurveyAdmin(admin.ModelAdmin):
    list_display = ["slug", "title", "status", "visibility", "deleted_at", "updated_at"]
    list_filter = ["status", "visibility", ("deleted_at", admin.EmptyFieldListFilter)]
    search_fields = ["slug", "title"]
    readonly_fields = ["created_at", "updated_at"]

    def get_queryset(self, request):
        # all_objects, not the default manager: staff have to be able to find a
        # survey they withdrew, both to read its responses and to undo it.
        return Survey.all_objects.get_queryset()


@admin.register(Response)
class ResponseAdmin(admin.ModelAdmin):
    list_display = ["survey", "submitted_by", "submitted_at"]
    list_filter = ["survey"]
    readonly_fields = ["survey", "data", "submitted_by", "submitted_at", "meta"]
