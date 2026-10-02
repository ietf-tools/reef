# Copyright The IETF Trust 2026, All Rights Reserved
import logging
import zlib

from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse

from reef.admin_readonly import ReadOnlyAdminMixin

from .changes import COMPARED, OLDER, SEEDED, load_snapshot
from .models import (
    DocumentSnapshot,
    PendingNotification,
    SimulationRun,
    SubjectNotificationEvent,
    Subscription,
    WebNotification,
)
from .simulate import identify
from .tasks import run_simulation

logger = logging.getLogger("reef")

# Red's index is about 17 MB; anything far beyond that is not one.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


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


class SimulateForm(forms.Form):
    holds = forms.ChoiceField(
        label="The file holds",
        choices=SimulationRun.Holds.choices,
        initial=SimulationRun.Holds.ENTRIES,
        widget=forms.RadioSelect,
    )
    index = forms.FileField(
        label="File",
        help_text=(
            "JSON. Compared with the live snapshot and subscriptions in the "
            "background; nothing else is saved, and the upload is discarded once "
            "the run finishes."
        ),
    )


def _require_permission(request):
    if not request.user.has_perm("subscriptions.view_subscription"):
        raise PermissionDenied


def simulate_view(request):
    """Upload an rfc-index.json to see what the next run would notify. Read-only.

    Only queues the run: parsing, validating and matching a whole index can outlast
    the request, so subscriptions.tasks.run_simulation does them and
    simulate_run_view is what a browser polls for the outcome.
    """
    _require_permission(request)
    form = SimulateForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["index"]
        if upload.size > MAX_UPLOAD_BYTES:
            form.add_error("index", "That file is larger than the index should be.")
        else:
            run = SimulationRun.objects.create(
                triggered_by=request.user,
                holds=form.cleaned_data["holds"],
                upload=zlib.compress(upload.read()),
            )
            # Logged on arrival, so that an upload which reached Django can be told
            # apart from one held up in front of it.
            logger.info(
                "simulate: run %s queued by %s: %s bytes of %s",
                run.pk,
                identify(request.user)[0],
                upload.size,
                run.holds,
            )
            run_simulation.delay(run.pk)
            return HttpResponseRedirect(
                reverse("admin:subscriptions-simulate-run", args=[run.pk])
            )
    return render(
        request,
        "admin/subscriptions/simulate.html",
        {
            **admin.site.each_context(request),
            "title": "Change detection simulator",
            "form": form,
            "recent_runs": SimulationRun.objects.defer(
                "upload", "result"
            ).select_related("triggered_by")[:10],
        },
    )


def simulate_run_view(request, run_id):
    """One simulation's progress while it runs, and its report once it has."""
    _require_permission(request)
    run = get_object_or_404(SimulationRun.objects.defer("upload"), pk=run_id)
    return render(
        request,
        "admin/subscriptions/simulate_run.html",
        {
            **admin.site.each_context(request),
            "title": "Change detection simulator",
            "run": run,
            "report": run.result,
            "SEEDED": SEEDED,
            "OLDER": OLDER,
            "COMPARED": COMPARED,
        },
    )


def _get_admin_urls(get_urls):
    def wrapper():
        return [
            path(
                "subscriptions/simulate/",
                admin.site.admin_view(simulate_view),
                name="subscriptions-simulate",
            ),
            path(
                "subscriptions/simulate/<int:run_id>/",
                admin.site.admin_view(simulate_run_view),
                name="subscriptions-simulate-run",
            ),
        ] + get_urls()

    return wrapper


admin.site.get_urls = _get_admin_urls(admin.site.get_urls)


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
