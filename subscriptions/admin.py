# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib import admin

from reef.admin_readonly import ReadOnlyAdminMixin

from .changes import load_snapshot
from .models import (
    DocumentSnapshot,
    PendingNotification,
    SubjectNotificationEvent,
    Subscription,
    WebNotification,
)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "kind",
        "params",
        "document_set",
        "subject",
        "created_at",
    ]
    list_filter = ["kind"]


@admin.register(WebNotification)
class WebNotificationAdmin(admin.ModelAdmin):
    list_display = ["user", "kind", "read", "created_at"]
    list_filter = ["kind", "read"]


# Read-only from here down: should be written by the notification pipeline's tasks only.


@admin.register(DocumentSnapshot)
class DocumentSnapshotAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ["created_on", "updated_at", "document_count"]
    fields = ["created_on", "updated_at", "document_count"]
    readonly_fields = fields

    @admin.display(description="Documents")
    def document_count(self, obj):
        snapshot = load_snapshot()
        return "unreadable" if snapshot is None else len(snapshot)


@admin.register(SubjectNotificationEvent)
class SubjectNotificationEventAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ["user", "event_kind", "event_key", "created_at"]
    list_filter = ["event_kind"]
    search_fields = ["user__username", "user__email", "event_key"]
    readonly_fields = [
        "user",
        "subscription_ids",
        "event_kind",
        "event_key",
        "event",
        "created_at",
    ]


@admin.register(PendingNotification)
class PendingNotificationAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ["user", "event_count", "attempts", "created_at", "sent_at"]
    search_fields = ["user__username", "user__email"]
    readonly_fields = [
        "user",
        "dedupe_key",
        "subscription_ids",
        "events",
        "attempts",
        "sent_at",
        "created_at",
    ]

    @admin.display(description="Events")
    def event_count(self, obj):
        return len(obj.events)
