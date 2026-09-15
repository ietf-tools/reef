# Copyright The IETF Trust 2026, All Rights Reserved
"""A staff-only admin page that runs `manage.py precompute` and shows its output.

No model backs this -- there is nothing here to list or edit, only an action to
trigger and a result to read -- so it is wired in as a plain admin_view rather than
through a ModelAdmin, the same way reefauth customises admin.site directly.
"""

import io

from django.contrib import admin
from django.core.management import CommandError, call_command
from django.shortcuts import render
from django.urls import path

from reef.locks import advisory_lock

from .tasks import LOCK_NAME


def precompute_view(request):
    result = None
    if request.method == "POST":
        with advisory_lock(LOCK_NAME) as acquired:
            if not acquired:
                result = {
                    "skipped": True,
                    "message": "Another precompute run is already in progress. "
                    "Try again shortly.",
                }
            else:
                out, err = io.StringIO(), io.StringIO()
                try:
                    call_command("precompute", stdout=out, stderr=err)
                    ok = True
                except CommandError as exc:
                    ok = False
                    err.write(str(exc))
                result = {
                    "skipped": False,
                    "ok": ok,
                    "output": out.getvalue(),
                    "error": err.getvalue(),
                }

    return render(
        request,
        "precomputer/run.html",
        {**admin.site.each_context(request), "title": "Precompute", "result": result},
    )


def _get_admin_urls(get_urls):
    def wrapper():
        return [
            path(
                "precompute/",
                admin.site.admin_view(precompute_view),
                name="precomputer-run",
            ),
        ] + get_urls()

    return wrapper


admin.site.get_urls = _get_admin_urls(admin.site.get_urls)
