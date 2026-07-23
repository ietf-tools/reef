# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin

from .models import Response, Survey


@admin.register(Survey)
class SurveyAdmin(admin.ModelAdmin):
    list_display = ["slug", "title", "status", "visibility", "updated_at"]
    list_filter = ["status", "visibility"]
    search_fields = ["slug", "title"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(Response)
class ResponseAdmin(admin.ModelAdmin):
    list_display = ["survey", "submitted_by", "submitted_at"]
    list_filter = ["survey"]
    readonly_fields = ["survey", "data", "submitted_by", "submitted_at", "meta"]
