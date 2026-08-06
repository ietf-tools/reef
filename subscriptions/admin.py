# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin

from .models import Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["user", "kind", "params", "verified", "created_at"]
    list_filter = ["kind", "verified"]
