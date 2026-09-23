# Copyright The IETF Trust 2026, All Rights Reserved
"""Server-rendered survey builder pages, under /admin/survey-builder/.

These pages host the self-hosted SurveyJS Creator and Analytics, and a readable
table of each survey's responses with a CSV export. Authoring and results are
staff-only; login goes through Authentik (LOGIN_URL).
"""

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import StreamingHttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods

from . import tabulate
from .models import Survey

RESPONSES_PER_PAGE = 50


def staff_required(view):
    """Require an authenticated staff user, redirecting to OIDC login otherwise."""
    return login_required(user_passes_test(lambda u: u.is_staff)(view))


@staff_required
def survey_list(request):
    surveys = Survey.objects.all()
    context = {
        **admin.site.each_context(request),
        "title": "Surveys",
        "surveys": surveys,
        "statuses": Survey.Status.choices,
    }
    return render(request, "surveys/list.html", context)


@staff_required
@require_http_methods(["POST"])
def survey_set_status(request, pk):
    survey = get_object_or_404(Survey, pk=pk)
    status = request.POST.get("status")
    if status in Survey.Status.values:
        survey.status = status
        survey.save()
    return redirect("manage-survey-list")


@staff_required
@require_http_methods(["POST"])
def survey_create(request):
    title = (request.POST.get("title") or "").strip()
    slug = slugify(request.POST.get("slug") or title)
    if not title or not slug:
        return redirect("manage-survey-list")
    if Survey.objects.filter(slug=slug).exists():
        return redirect("manage-survey-list")
    survey = Survey.objects.create(
        slug=slug,
        title=title,
        status=Survey.Status.DRAFT,
        created_by=request.user,
    )
    return redirect("manage-survey-edit", pk=survey.pk)


@staff_required
def survey_edit(request, pk):
    survey = get_object_or_404(Survey, pk=pk)
    config = {
        "apiUrl": f"/api/reef/surveys/{survey.pk}/",
        "definition": survey.definition or {},
        "theme": survey.theme,
        "csrfToken": get_token(request),
        "licenseKey": settings.REEF_SURVEYJS_LICENSE_KEY,
    }
    context = {
        **admin.site.each_context(request),
        "title": f"Edit: {survey.title}",
        "survey": survey,
        "config": config,
    }
    return render(request, "surveys/creator.html", context)


@staff_required
def survey_analytics(request, pk):
    survey = get_object_or_404(Survey, pk=pk)
    config = {
        "resultsUrl": f"/api/reef/surveys/{survey.pk}/results/",
        "licenseKey": settings.REEF_SURVEYJS_LICENSE_KEY,
    }
    context = {
        **admin.site.each_context(request),
        "title": f"Results: {survey.title}",
        "survey": survey,
        "config": config,
    }
    return render(request, "surveys/analytics.html", context)


@staff_required
def survey_results_list(request):
    # all_objects: a withdrawn survey keeps its responses so they can still be read.
    surveys = Survey.all_objects.annotate(response_count=Count("responses")).order_by(
        "deleted_at", "-updated_at"
    )
    context = {
        **admin.site.each_context(request),
        "title": "Survey results",
        "surveys": surveys,
    }
    return render(request, "surveys/results_list.html", context)


@staff_required
def survey_responses(request, pk):
    survey = get_object_or_404(Survey.all_objects, pk=pk)
    columns = tabulate.columns_for(survey)
    responses = survey.responses.select_related("submitted_by").order_by(
        "-submitted_at", "-pk"
    )
    page = Paginator(responses, RESPONSES_PER_PAGE).get_page(request.GET.get("page"))
    rows = [
        {
            "response": response,
            "respondent": tabulate.respondent_label(response),
            "cells": [
                tabulate.cell_html(cell)
                for cell in tabulate.format_row(columns, response.data)
            ],
        }
        for response in page
    ]
    context = {
        **admin.site.each_context(request),
        "title": f"Responses: {survey.title}",
        "survey": survey,
        "columns": columns,
        "rows": rows,
        "page": page,
        "page_range": page.paginator.get_elided_page_range(page.number),
    }
    return render(request, "surveys/responses.html", context)


@staff_required
def survey_responses_csv(request, pk):
    survey = get_object_or_404(Survey.all_objects, pk=pk)
    columns = tabulate.columns_for(survey)
    response = StreamingHttpResponse(
        tabulate.iter_csv(survey, columns), content_type="text/csv; charset=utf-8"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{survey.slug}-responses.csv"'
    )
    return response
