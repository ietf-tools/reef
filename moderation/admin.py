# Copyright The IETF Trust 2026, All Rights Reserved
import uuid

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import path

from docsets.models import DocumentSet
from reef.admin_readonly import ReadOnlyAdminMixin
from reefauth.models import User
from surveys.models import Response

from . import actions, review
from .models import ModerationAction, ModerationFlag

PAGE_SIZE = 50
ACTIONS_PAGE_SIZE = 20
SNIPPET_LENGTH = 300


@admin.register(ModerationAction)
class ModerationActionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """The moderator log: what was done, by whom, to what, and why. Read only."""

    list_display = [
        "created_at",
        "action",
        "target_type",
        "target_id",
        "subject_name",
        "actor_name",
    ]
    list_filter = ["action", "target_type"]
    search_fields = ["target_id", "subject_sub", "subject_name", "actor_name", "reason"]
    date_hierarchy = "created_at"


@admin.register(ModerationFlag)
class ModerationFlagAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Moderators' verdicts, kept as labelled examples. Read only: they are made
    from the lists of sets and responses."""

    list_display = ["created_at", "target_type", "verdict", "classifier", "score"]
    list_filter = ["verdict", "target_type", "classifier"]
    search_fields = ["target_id", "text"]


def _ids(request, name, convert):
    ids = []
    for value in request.POST.getlist(name):
        try:
            ids.append(convert(value))
        except ValueError:
            continue
    return ids


def _moderate(request):
    verdict_by_button = {
        "spam": ModerationFlag.Verdict.SPAM,
        "fine": ModerationFlag.Verdict.FINE,
    }
    button = request.POST.get("button")
    reason = request.POST.get("reason", "")
    sets = DocumentSet.objects.filter(pk__in=_ids(request, "sets", uuid.UUID))
    responses = Response.objects.filter(pk__in=_ids(request, "responses", int))
    if button in verdict_by_button:
        verdict = verdict_by_button[button]
        with transaction.atomic():
            done = review.review_sets(request.user, sets, verdict, reason)
            done += review.review_responses(request.user, responses, verdict, reason)
        messages.success(request, f"Marked {done} as {verdict}.")
    elif button == "block":
        owners = {document_set.owner for document_set in sets.select_related("owner")}
        owners |= {
            response.submitted_by
            for response in responses.select_related("submitted_by")
            if response.submitted_by is not None
        }
        try:
            with transaction.atomic():
                blocked = sum(
                    1
                    for user in owners
                    if actions.block_user(
                        request.user, user, reason, remove_content=True
                    )
                )
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(
                request, f"Blocked {blocked} account(s) and removed their content."
            )
    elif button == "unblock":
        users = User.objects.filter(pk__in=_ids(request, "users", int))
        with transaction.atomic():
            lifted = sum(
                1 for user in users if actions.unblock_user(request.user, user, reason)
            )
        messages.success(request, f"Unblocked {lifted} account(s).")


def _page(request, queryset, param, per_page=PAGE_SIZE):
    """One page of a list, with links that leave the other lists where they are."""
    page = Paginator(queryset, per_page).get_page(request.GET.get(param))

    def link(number):
        query = request.GET.copy()
        query[param] = number
        return f"?{query.urlencode()}"

    page.previous_url = link(page.previous_page_number()) if page.has_previous() else ""
    page.next_url = link(page.next_page_number()) if page.has_next() else ""
    return page


def moderation_view(request):
    if request.method == "POST":
        _moderate(request)
        return redirect("admin:moderation")

    sets = (
        DocumentSet.objects.exclude(pk__in=review.reviewed_target_ids("documentset"))
        .select_related("owner")
        .order_by("-created_at", "-pk")
    )
    responses = (
        Response.objects.exclude(pk__in=review.reviewed_target_ids("response"))
        .select_related("survey", "submitted_by")
        .order_by("-submitted_at", "-pk")
    )
    blocked = User.objects.filter(blocked_at__isnull=False).order_by(
        "-blocked_at", "-pk"
    )
    response_page = _page(request, responses, "responses_page")
    context = {
        **admin.site.each_context(request),
        "title": "Moderation",
        "sets_page": _page(request, sets, "sets_page"),
        "responses_page": response_page,
        "response_rows": [
            {
                "response": response,
                "text": review.response_text(response)[:SNIPPET_LENGTH],
            }
            for response in response_page
        ],
        "blocked_page": _page(request, blocked, "blocked_page"),
        "actions_page": _page(
            request, ModerationAction.objects.all(), "actions_page", ACTIONS_PAGE_SIZE
        ),
    }
    return render(request, "admin/moderation/moderation.html", context)


def _get_admin_urls(get_urls):
    def wrapper():
        # Ahead of the app's own index at the same path, so that "Moderation" in
        # the admin lands on this page rather than a bare list of the log.
        return [
            path(
                "moderation/",
                admin.site.admin_view(moderation_view),
                name="moderation",
            ),
        ] + get_urls()

    return wrapper


admin.site.get_urls = _get_admin_urls(admin.site.get_urls)
