# Copyright The IETF Trust 2026, All Rights Reserved
from django import forms
from django.contrib import admin, messages
from django.contrib.admin.views.main import ChangeList
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST

from reef.admin_documents import DocumentTitleMixin

from .merge import MergeError, merge_and_notify
from .models import (
    PATH_SEPARATOR,
    Subject,
    SubjectAlias,
    SubjectAssignment,
    SubjectSyncRun,
)
from .tasks import run_subject_sync
from .tree import rollup


class SubjectParentField(forms.ModelChoiceField):
    """A parent picker labelled by path rather than by name.

    The autocomplete labels rows with __str__, which is the bare name, and a bare
    name is ambiguous exactly where it matters: "send" says nothing about which
    branch it belongs to. __str__ itself is left alone, because the merge notice and
    the mail template read it and a reader is not owed a path.
    """

    def label_from_instance(self, obj):
        return obj.path


class SubjectAdminForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = "__all__"
        field_classes = {"parent": SubjectParentField}


class SubjectMergeForm(forms.Form):
    target = SubjectParentField(queryset=Subject.objects.none(), label="Merge into")

    def __init__(self, source, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].queryset = Subject.objects.exclude(
            Q(pk=source.pk)
            | Q(path=source.path)
            | Q(path__startswith=source.path + PATH_SEPARATOR)
        ).order_by("path")


class RootSubjectFilter(admin.SimpleListFilter):
    """Filter by the top of the branch rather than by the subject.

    A filter per subject is a link per subject, which a vocabulary of several
    hundred cannot afford. The roots are fourteen or so, and a prefix match on the
    path narrows to the branch.
    """

    title = "top-level subject"
    parameter_name = "root"

    def lookups(self, request, model_admin):
        roots = Subject.all_objects.roots().order_by("path")
        return [(subject.slug, subject.name) for subject in roots]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        # The separator on the prefix, for the reason at_or_under() appends one:
        # security must not sweep in security-and-privacy.
        return queryset.filter(
            Q(subject__path=self.value())
            | Q(subject__path__startswith=self.value() + PATH_SEPARATOR)
        )


class SubjectAssignmentInline(DocumentTitleMixin, admin.TabularInline):
    model = SubjectAssignment
    extra = 0
    # Read-only rather than a column: an inline renders fields, and a title is not
    # one of the model's.
    readonly_fields = ["document_title"]


class SubjectChangeList(ChangeList):
    """Puts the deep count on each row of the page being rendered.

    One roll-up for the page rather than a subtree query per row, and hung on the
    objects rather than on the ModelAdmin, which Django keeps one of for the whole
    process and would therefore have two concurrent staff sharing a number.
    """

    def get_results(self, request):
        super().get_results(request)
        _, covered = rollup()
        for obj in self.result_list:
            obj._covered = len(covered.get(obj.path, ()))


class SubjectChildInline(admin.TabularInline):
    """The subjects immediately beneath this one, and a place to add another.

    fk_name because Subject points at itself twice and the two mean opposite
    things: parent is containment, merged_into is a redirect to where a retired
    subject went.
    """

    model = Subject
    fk_name = "parent"
    extra = 0
    fields = ["slug", "name", "retired_at"]
    show_change_link = True
    verbose_name = "subject beneath this one"
    verbose_name_plural = "subjects beneath this one"


class SubjectAliasInline(admin.TabularInline):
    """The other names for a subject, curated where the subject is.

    Not an admin of its own: an alias is meaningless apart from what it points at, and
    the question staff have is always "what else should reach this subject" rather
    than "what aliases exist". The list below shows renames arriving here by
    themselves, which is the other reason this is the place to see them.
    """

    model = SubjectAlias
    extra = 0
    fields = ["slug", "created_at"]
    readonly_fields = ["created_at"]


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    """Where the vocabulary is curated, which is the only place it is.

    Nothing self-serves a subject into existence: readers subscribe to
    subjects and Red renders them, but both work from the list made here.
    """

    form = SubjectAdminForm
    list_display = [
        "indented_name",
        "slug",
        "assignment_count",
        "covered_count",
        "alias_list",
        "retired_at",
        "merged_into",
    ]
    list_display_links = ["indented_name"]
    list_filter = [("retired_at", admin.EmptyFieldListFilter), "depth"]
    # Tree order, so children sit under the parent they hang from rather than
    # being scattered through an alphabetical list of several hundred names.
    ordering = ["path"]
    autocomplete_fields = ["parent"]
    actions = [
        "retire_selected",
        "unretire_selected",
        "retire_subtree_selected",
        "merge_selected",
    ]
    # Typing the name fills the slug, which is the order the two are decided
    # in. It stops filling once the subject has been saved, which is right: a
    # rename leaves the old slug behind as an alias, and growing one of those
    # every time somebody reworded a name would be noise rather than history.
    prepopulated_fields = {"slug": ["name"]}
    # assignments__doc, because the question a curator arrives with is usually
    # about a document rather than a subject. aliases__slug, so that searching the
    # name a reader typed finds the subject it resolves to, which is the question an
    # alias exists to answer.
    search_fields = [
        "name",
        "slug",
        "path",
        "description",
        "assignments__doc",
        "aliases__slug",
    ]
    inlines = [SubjectChildInline, SubjectAliasInline, SubjectAssignmentInline]

    def get_queryset(self, request):
        # all_objects, because staff have to be able to find a retired subject in
        # order to bring it back or see where it went. Annotated rather than counted
        # per row: the column is on the listing, so counting in the display method
        # would be one query per subject.
        return Subject.all_objects.annotate(
            _assignments=Count("assignments", distinct=True)
        ).prefetch_related("aliases")

    def get_changelist(self, request, **kwargs):
        return SubjectChangeList

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/merge/",
                self.admin_site.admin_view(self.merge_view),
                name="subjects_subject_merge",
            ),
            path(
                "sync/",
                self.admin_site.admin_view(self.sync_view),
                name="subjects_subject_sync",
            ),
            path(
                "sync/runs/<int:run_id>/",
                self.admin_site.admin_view(self.sync_run_view),
                name="subjects_subject_sync_run",
            ),
            path(
                "sync/merge/<int:source_id>/<int:target_id>/",
                # require_POST wraps the bound method here, not the method
                # definition below: decorating the unbound function would make
                # its request_method_list check run against `self` (the
                # instance Python binds first), not the actual request.
                self.admin_site.admin_view(require_POST(self.sync_merge_view)),
                name="subjects_subject_sync_merge",
            ),
        ]
        return custom_urls + urls

    @admin.action(description="Merge selected subject into another subject")
    def merge_selected(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                "Select exactly one subject to merge.",
                level=messages.ERROR,
            )
            return None
        source = queryset.get()
        return HttpResponseRedirect(
            reverse("admin:subjects_subject_merge", args=[source.pk])
        )

    def merge_view(self, request, object_id):
        source = get_object_or_404(Subject.all_objects, pk=object_id)
        form = SubjectMergeForm(source, request.POST or None)
        if request.method == "POST" and form.is_valid():
            target = form.cleaned_data["target"]
            try:
                affected = merge_and_notify(source, target)
            except MergeError as exc:
                form.add_error("target", str(exc))
            else:
                self.message_user(
                    request,
                    f"Merged {source} into {target}; {len(affected)} "
                    f"subscriber(s) will be told in the next daily digest.",
                )
                return HttpResponseRedirect(
                    reverse("admin:subjects_subject_changelist")
                )
        context = {
            **self.admin_site.each_context(request),
            "title": "Merge subject",
            "source": source,
            "form": form,
            "changelist_url": reverse("admin:subjects_subject_changelist"),
        }
        return render(request, "admin/subjects/subject/merge.html", context)

    def sync_view(self, request):
        """Start a mirror of the vocabulary and assignments from
        rfc-editor/rfc-subject-tags.

        See subjects/sync.py for the sync itself and plan.md for why this
        can't just run inline: a real first sync against an empty vocabulary
        once outran the gunicorn worker timeout, taking minutes and leaving
        the request with nothing to show for it. So this only ever creates a
        SubjectSyncRun and hands it to Celery (subjects/tasks.py); the actual
        run happens off the request, and sync_run_view is what a browser polls
        for its outcome.
        """
        if request.method == "POST":
            run = SubjectSyncRun.objects.create(
                confirm_large_change=bool(request.POST.get("confirm_large_change")),
                triggered_by=request.user,
            )
            run_subject_sync.delay(run.pk)
            return HttpResponseRedirect(
                reverse("admin:subjects_subject_sync_run", args=[run.pk])
            )
        context = {
            **self.admin_site.each_context(request),
            "title": "Sync subject tags",
            "recent_runs": SubjectSyncRun.objects.select_related("triggered_by")[:10],
            "changelist_url": reverse("admin:subjects_subject_changelist"),
        }
        return render(request, "admin/subjects/subject/sync.html", context)

    def sync_run_view(self, request, run_id):
        """One sync run's status -- a browser polls this while it's in
        progress, via the plain <meta refresh> in the template, and it renders
        the full result once it's finished.

        A POST here is the "apply anyway" resubmission from a
        needs_confirmation result: a fresh run, not a mutation of this one, so
        that this row stays a true record of what the safety threshold saw
        before anyone acted on it.
        """
        run = get_object_or_404(SubjectSyncRun, pk=run_id)
        if request.method == "POST":
            confirmed = SubjectSyncRun.objects.create(
                confirm_large_change=True, triggered_by=request.user
            )
            run_subject_sync.delay(confirmed.pk)
            return HttpResponseRedirect(
                reverse("admin:subjects_subject_sync_run", args=[confirmed.pk])
            )

        result = run.result
        retired = None
        if result and result.get("written"):
            # Joined here rather than in the template, which has no way to
            # look a dict up by a loop variable's value.
            pks = dict(
                Subject.all_objects.filter(slug__in=result["retired"]).values_list(
                    "slug", "pk"
                )
            )
            retired = [
                {
                    "slug": slug,
                    "pk": pks.get(slug),
                    "suggestions": result.get("suggestions", {}).get(slug, []),
                }
                for slug in result["retired"]
            ]
        context = {
            **self.admin_site.each_context(request),
            "title": "Sync subject tags",
            "run": run,
            "result": result,
            "retired": retired,
            "changelist_url": reverse("admin:subjects_subject_changelist"),
        }
        return render(request, "admin/subjects/subject/sync_run.html", context)

    def sync_merge_view(self, request, source_id, target_id):
        """Act on a sync result's suggested successor for a retired subject.

        merge_subjects() refuses an already-retired source (see subjects/merge.py
        and plan.md), so this unretires first -- from the subject's own history
        this looks like "somebody unretired it and merged it a moment later",
        because that is exactly what happened, just in one click. A second click
        after the source has already been merged elsewhere finds nothing left to
        move and notifies nobody a second time, so it is harmless rather than an
        error.
        """
        source = get_object_or_404(Subject.all_objects, pk=source_id)
        target = get_object_or_404(Subject.all_objects, pk=target_id)
        try:
            with transaction.atomic():
                if source.is_retired:
                    source.unretire()
                affected = merge_and_notify(source, target)
        except MergeError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
        else:
            self.message_user(
                request,
                f"Merged {source} into {target}; {len(affected)} "
                f"subscriber(s) will be told in the next daily digest.",
            )
        return HttpResponseRedirect(reverse("admin:subjects_subject_changelist"))

    @admin.display(description="Subject", ordering="path")
    def indented_name(self, obj):
        if not obj.depth:
            return obj.name
        return format_html(
            "{}{}", mark_safe("&nbsp;" * 4 * obj.depth + "&#8627; "), obj.name
        )

    @admin.display(description="Covered")
    def covered_count(self, obj):
        """Documents under this subject and everything beneath it, deduplicated.

        Beside the direct count rather than replacing it, because the two say
        different things and a branch where they differ is the normal case.
        """
        return getattr(obj, "_covered", obj._assignments)

    @admin.action(description="Retire selected subjects")
    def retire_selected(self, request, queryset):
        """Stop offering a subject without cutting off the people following it.

        Not a merge: their subscriptions go on matching whatever the subject still
        covers. Use the merge action instead when the followers should end up
        somewhere.
        """
        retired, refused = [], []
        for subject in queryset:
            if subject.is_retired:
                continue
            try:
                subject.retire()
            except ValidationError as exc:
                refused.append(f"{subject.slug}: {exc.messages[0]}")
            else:
                retired.append(subject)
        self.message_user(request, f"Retired {len(retired)} subject(s).")
        for message in refused:
            self.message_user(request, message, level=messages.WARNING)

    @admin.action(description="Retire selected subjects and everything beneath them")
    def retire_subtree_selected(self, request, queryset):
        """Retire whole branches.

        The action retire_selected refuses a subject with live children, because a
        live child of a retired parent is still offered and has nowhere to be drawn
        in a picker. This is how staff say they meant the branch.
        """
        retired = 0
        for subject in queryset:
            if subject.is_retired:
                continue
            retired += 1 + Subject.objects.under(subject).count()
            subject.retire(subtree=True)
        self.message_user(request, f"Retired {retired} subject(s).")

    @admin.action(description="Bring selected subjects back")
    def unretire_selected(self, request, queryset):
        restored = [subject for subject in queryset if subject.is_retired]
        for subject in restored:
            subject.unretire()
        self.message_user(request, f"Restored {len(restored)} subject(s).")

    @admin.display(description="Documents", ordering="_assignments")
    def assignment_count(self, obj):
        return obj._assignments

    @admin.display(description="Also known as")
    def alias_list(self, obj):
        # Prefetched above, so this is not a query per row.
        return ", ".join(alias.slug for alias in obj.aliases.all())


@admin.register(SubjectAssignment)
class SubjectAssignmentAdmin(DocumentTitleMixin, admin.ModelAdmin):
    """Assignments on their own, for working document-first.

    The inline above is for curating one subject. This is for the other
    direction, where what is in hand is a document and the question is which
    subjects it should carry.
    """

    list_display = ["doc", "document_title", "subject_path", "assigned_at"]
    list_filter = [RootSubjectFilter]
    autocomplete_fields = ["subject"]
    search_fields = ["doc", "subject__path", "subject__name"]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("subject")

    @admin.display(description="Subject", ordering="subject__path")
    def subject_path(self, obj):
        """The whole path, not the bare name.

        This is the column that catches a mis-filing. A curator who sees
        messaging/email/pop3 beside a Diffie-Hellman RFC notices; one who sees
        pop3 does not.
        """
        return obj.subject.path


@admin.register(SubjectSyncRun)
class SubjectSyncRunAdmin(admin.ModelAdmin):
    """History of "Sync subject tags" runs. Read-only -- a run is a record of
    what subjects.tasks.run_subject_sync did, not something curated here.

    The changelist is a second way to reach a run beside the list on the sync
    page itself (subjects/subject/sync/), useful once that page's own
    "recent runs" window has scrolled a run out of it.
    """

    list_display = [
        "__str__",
        "confirm_large_change",
        "triggered_by",
        "started_at",
        "finished_at",
    ]
    list_filter = ["status"]
    readonly_fields = [
        "status",
        "confirm_large_change",
        "triggered_by",
        "created_at",
        "started_at",
        "finished_at",
        "progress_message",
        "result",
        "error",
    ]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Runs are a small, slow-growing audit trail (one per button click),
        # not something that needs pruning by hand.
        return False
