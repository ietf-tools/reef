# Copyright The IETF Trust 2026, All Rights Reserved
from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import path

from .changes import COMPARED, OLDER, SEEDED
from .models import Subscription, WebNotification
from .simulate import read_payload, simulate

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
    index = forms.FileField(
        label="rfc-index.json",
        help_text=(
            "Red's /api/v1/rfc-index.json, or an edited copy. Read in memory and "
            "compared with the live snapshot and subscriptions; nothing is saved."
        ),
    )


def simulate_view(request):
    """What the next run would notify for an uploaded rfc-index.json. Read-only."""
    if not request.user.has_perm("subscriptions.view_subscription"):
        raise PermissionDenied
    report = None
    form = SimulateForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["index"]
        if upload.size > MAX_UPLOAD_BYTES:
            form.add_error("index", "That file is larger than the index should be.")
        else:
            payload, problem = read_payload(upload)
            if problem:
                form.add_error("index", problem)
            else:
                report = simulate(payload)
    return render(
        request,
        "admin/subscriptions/simulate.html",
        {
            **admin.site.each_context(request),
            "title": "Change detection simulator",
            "form": form,
            "report": report,
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
        ] + get_urls()

    return wrapper


admin.site.get_urls = _get_admin_urls(admin.site.get_urls)
