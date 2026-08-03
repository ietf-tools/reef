# Copyright The IETF Trust 2026, All Rights Reserved
"""Server-rendered survey builder pages (the private /manage/ site).

These pages host the self-hosted SurveyJS Creator. Authoring and results are
staff-only; login goes through Authentik (LOGIN_URL).
"""

from django.conf import settings
from django.contrib.auth.decorators import login_required, user_passes_test
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods

from .models import Survey


def staff_required(view):
    """Require an authenticated staff user, redirecting to OIDC login otherwise."""
    return login_required(user_passes_test(lambda u: u.is_staff)(view))


@staff_required
def survey_list(request):
    surveys = Survey.objects.all()
    return render(
        request,
        "surveys/list.html",
        {"surveys": surveys, "statuses": Survey.Status.choices},
    )


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
    return render(request, "surveys/creator.html", {"survey": survey, "config": config})


@staff_required
def survey_analytics(request, pk):
    survey = get_object_or_404(Survey, pk=pk)
    config = {
        "resultsUrl": f"/api/reef/surveys/{survey.pk}/results/",
        "licenseKey": settings.REEF_SURVEYJS_LICENSE_KEY,
    }
    return render(
        request, "surveys/analytics.html", {"survey": survey, "config": config}
    )
