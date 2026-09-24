# Copyright The IETF Trust 2026, All Rights Reserved
"""The popularity page, and the rankings behind it, readable but never edited here.

Both tables are outputs: MatomoRanking of an upload, DocumentPopularity of a
recompute. A row typed in by hand would be overwritten by the next of either, so
the admin shows them and refuses to change them. The two timestamp columns are what
tell an admin the ranking is being refreshed. The page at /admin/popularity/ is
where an input is fed: today that is a Matomo export, uploaded there. It is wired
in as plain admin views, the way the precomputer's button is, since it belongs to
no one table.
"""

import json

from django import forms
from django.contrib import admin, messages
from django.core.files.uploadhandler import MemoryFileUploadHandler, StopUpload
from django.db import transaction
from django.db.models import Max
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.views.decorators.csrf import csrf_exempt, csrf_protect

from reef.admin_documents import DocumentTitleMixin

from .compute import replace_ranking
from .matomo import parse_rankings
from .models import DocumentPopularity, MatomoImportRun, MatomoRanking
from .tasks import publish_matomo_import

# Well above any export seen so far (under 2 MB) and well below what one gunicorn
# worker can hold while parsing it, since the whole file is parsed in memory.
MATOMO_UPLOAD_MAX_BYTES = 50 * 1024 * 1024


class MatomoUploadHandler(MemoryFileUploadHandler):
    """Keep the upload in memory whatever its size, up to the cap.

    Django's default handlers stream anything over FILE_UPLOAD_MAX_MEMORY_SIZE to
    a temporary file on disk. The export holds personal data that must exist in one
    process for one request and nowhere else, so this is the only handler
    installed, and a file over the cap stops the upload rather than spilling.
    """

    def __init__(self, request=None):
        super().__init__(request)
        self.received = 0
        self.overflowed = False

    def handle_raw_input(
        self, input_data, META, content_length, boundary, encoding=None
    ):
        self.activated = True

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > MATOMO_UPLOAD_MAX_BYTES:
            self.overflowed = True
            raise StopUpload()
        return super().receive_data_chunk(raw_data, start)


class MatomoUploadForm(forms.Form):
    export = forms.FileField(
        label="Matomo export",
        help_text=(
            "The JSON of Actions.getPageUrls, expanded. Only the RFC identifiers "
            "and their relative ranking are kept from it."
        ),
    )

    def clean_export(self):
        upload = self.cleaned_data["export"]
        try:
            payload = json.load(upload)
        except ValueError as exc:
            # A decode error's message holds a position and nothing from the file.
            raise forms.ValidationError(f"Not valid JSON: {exc}") from exc
        parsed = parse_rankings(payload)
        if not parsed.scores:
            raise forms.ValidationError(
                "No RFC page was found under the info, rfc or pdfrfc directories, "
                "so nothing was replaced."
            )
        return parsed


class ReadOnlyRankingAdmin(DocumentTitleMixin, admin.ModelAdmin):
    document_field = "rfc"

    list_display = ["rfc", "document_title", "score", "created_at", "updated_at"]
    search_fields = ["rfc"]
    ordering = ["-score", "rfc"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocumentPopularity)
class DocumentPopularityAdmin(ReadOnlyRankingAdmin):
    pass


@admin.register(MatomoRanking)
class MatomoRankingAdmin(ReadOnlyRankingAdmin):
    pass


@csrf_exempt
def popularity_view(request):
    """The popularity page: what is ranked, and the inputs it is computed from.

    Matomo is the one input so far, and its upload form lives here. The parse
    happens in the request, so that the raw export never reaches the broker or a
    worker; the task is handed a run id. The upload handler has to be installed
    before the body is read, and csrf_protect reads request.POST, hence this
    exempt view installing it and then delegating to a protected one.
    """
    handler = MatomoUploadHandler(request)
    request.upload_handlers = [handler]
    return _popularity_view(request, handler)


@csrf_protect
def _popularity_view(request, handler):
    form = MatomoUploadForm()
    if request.method == "POST":
        # Reading the body is what runs the handler, so it comes before the
        # check on what the handler saw.
        data, files = request.POST, request.FILES
        if handler.overflowed:
            messages.error(
                request,
                f"The export is larger than the {MATOMO_UPLOAD_MAX_BYTES // 2**20}"
                " MB upload cap. Nothing was replaced.",
            )
        else:
            form = MatomoUploadForm(data, files)
    if form.is_bound and form.is_valid():
        parsed = form.cleaned_data["export"]
        with transaction.atomic():
            replace_ranking(MatomoRanking, parsed.scores)
            run = MatomoImportRun.objects.create(
                triggered_by=request.user,
                rows_seen=parsed.rows_seen,
                rfcs_ranked=len(parsed.scores),
                rows_ignored=parsed.rows_ignored,
                truncated=parsed.truncated,
            )
        publish_matomo_import.delay(run.pk)
        return HttpResponseRedirect(reverse("admin:popularity-run", args=[run.pk]))
    if form.is_bound:
        messages.error(request, "Nothing was replaced.")
    context = {
        **admin.site.each_context(request),
        "title": "Popularity",
        "form": form,
        "ranked_count": DocumentPopularity.objects.count(),
        "computed_at": DocumentPopularity.objects.aggregate(latest=Max("updated_at"))[
            "latest"
        ],
        "matomo_count": MatomoRanking.objects.count(),
        "recent_runs": MatomoImportRun.objects.select_related("triggered_by")[:10],
    }
    return render(request, "admin/popularity/popularity.html", context)


def popularity_run_view(request, run_id):
    run = get_object_or_404(MatomoImportRun, pk=run_id)
    context = {
        **admin.site.each_context(request),
        "title": "Popularity",
        "run": run,
    }
    return render(request, "admin/popularity/popularity_run.html", context)


def _get_admin_urls(get_urls):
    def wrapper():
        # Ahead of the app's own index at the same path, so that "Popularity" in
        # the admin lands on this page rather than a bare list of two tables.
        return [
            path(
                "popularity/",
                admin.site.admin_view(popularity_view),
                name="popularity",
            ),
            path(
                "popularity/runs/<int:run_id>/",
                admin.site.admin_view(popularity_run_view),
                name="popularity-run",
            ),
        ] + get_urls()

    return wrapper


admin.site.get_urls = _get_admin_urls(admin.site.get_urls)
