# Copyright The IETF Trust 2026, All Rights Reserved
"""A staff-only admin page that starts `manage.py precompute` and shows its output.

A full run renders and uploads several hundred files, which on a deployment takes
over a minute -- long enough that a request waiting on it showed nothing but a
spinner and was heading for the gunicorn timeout. So the button only creates a
PrecomputeRun and hands it to Celery (precomputer.tasks.precompute_from_admin), and
the run's page polls that row, as the subject sync's button already does.

There is still nothing here to list or edit, so it is wired in as plain admin views
rather than through a ModelAdmin, the same way reefauth customises admin.site
directly.
"""

from django.contrib import admin
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse

from .models import PrecomputeRun
from .tasks import precompute_from_admin


def precompute_view(request):
    if request.method == "POST":
        run = PrecomputeRun.objects.create(triggered_by=request.user)
        precompute_from_admin.delay(run.pk)
        return HttpResponseRedirect(
            reverse("admin:precomputer-run-detail", args=[run.pk])
        )
    return render(
        request,
        "precomputer/run.html",
        {
            **admin.site.each_context(request),
            "title": "Precompute",
            "recent_runs": PrecomputeRun.objects.select_related("triggered_by")[:10],
        },
    )


def precompute_run_view(request, run_id):
    """One run's status, refreshed by the template's <meta refresh> until it has
    finished, then its full output."""
    run = get_object_or_404(PrecomputeRun, pk=run_id)
    return render(
        request,
        "precomputer/run_detail.html",
        {**admin.site.each_context(request), "title": "Precompute", "run": run},
    )


def _get_admin_urls(get_urls):
    def wrapper():
        return [
            path(
                "precompute/",
                admin.site.admin_view(precompute_view),
                name="precomputer-run",
            ),
            path(
                "precompute/runs/<int:run_id>/",
                admin.site.admin_view(precompute_run_view),
                name="precomputer-run-detail",
            ),
        ] + get_urls()

    return wrapper


admin.site.get_urls = _get_admin_urls(admin.site.get_urls)
