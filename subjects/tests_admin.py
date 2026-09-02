# Copyright The IETF Trust 2026, All Rights Reserved
"""Curating a tree in the admin.

Through the real views rather than by poking the ModelAdmin, because most of what
changed here is what staff see: the order of the listing, what a parent picker is
labelled with, and which filters a vocabulary of several hundred can afford.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .admin import RootSubjectFilter, SubjectAdminForm
from .models import Subject, SubjectAssignment
from .tests_hierarchy import tree

User = get_user_model()

BRANCH = (
    "messaging",
    "messaging/email",
    "messaging/email/smtp",
    "security",
    "security-and-privacy",
)


class SubjectAdminTestCase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(
            username="staff", oidc_sub="staff", password="x"
        )
        self.client.force_login(self.staff)
        self.made = tree(*BRANCH)
        SubjectAssignment.objects.create(subject=self.made["smtp"], doc="rfc5321")


class ChangelistTests(SubjectAdminTestCase):
    def test_the_listing_is_in_tree_order(self):
        response = self.client.get(reverse("admin:subjects_subject_changelist"))
        slugs = [obj.slug for obj in response.context["cl"].result_list]
        self.assertEqual(
            slugs,
            ["messaging", "email", "smtp", "security", "security-and-privacy"],
        )

    def test_a_child_is_indented_under_its_parent(self):
        response = self.client.get(reverse("admin:subjects_subject_changelist"))
        self.assertContains(response, "&#8627;")

    def test_the_deep_count_is_on_the_row(self):
        response = self.client.get(reverse("admin:subjects_subject_changelist"))
        rows = {obj.slug: obj._covered for obj in response.context["cl"].result_list}
        # messaging holds nothing itself and covers what smtp holds.
        self.assertEqual(rows["messaging"], 1)
        self.assertEqual(rows["smtp"], 1)
        self.assertEqual(rows["security"], 0)

    def test_the_deep_counts_do_not_cost_a_query_per_row(self):
        """The count of queries does not grow with the size of the vocabulary.

        Asserted as a comparison rather than as a number, because the number is
        Django's business and the invariant is ours: one roll-up for the page, not
        a subtree query per row.
        """
        url = reverse("admin:subjects_subject_changelist")
        self.client.get(url)  # warm anything cached for the session
        before = self._render_cost(url)

        for number in range(30):
            Subject.objects.create(name=f"S{number}", slug=f"s{number}")
        self.assertEqual(self._render_cost(url), before)

    def _render_cost(self, url):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            self.client.get(url)
        return len(captured.captured_queries)

    def test_searching_by_path_finds_a_branch(self):
        url = reverse("admin:subjects_subject_changelist")
        response = self.client.get(url, {"q": "messaging/email"})
        self.assertEqual(
            sorted(obj.slug for obj in response.context["cl"].result_list),
            ["email", "smtp"],
        )


class ParentPickerTests(SubjectAdminTestCase):
    def test_the_parent_is_labelled_by_path_not_by_name(self):
        # A bare name is ambiguous exactly where it matters: "smtp" says nothing
        # about which branch it hangs from.
        field = SubjectAdminForm().fields["parent"]
        self.assertEqual(
            field.label_from_instance(self.made["smtp"]), "messaging/email/smtp"
        )

    def test_str_is_left_alone_for_the_mail_and_the_merge_notice(self):
        self.assertEqual(str(self.made["smtp"]), "Smtp")

    def test_the_form_refuses_a_cycle(self):
        form = SubjectAdminForm(
            instance=self.made["messaging"],
            data={
                "slug": "messaging",
                "name": "Messaging",
                "description": "",
                "parent": self.made["smtp"].pk,
            },
        )
        self.assertFalse(form.is_valid())
        self.assertIn("parent", form.errors)

    def test_the_form_refuses_a_retired_parent(self):
        self.made["security"].retire()
        form = SubjectAdminForm(
            data={
                "slug": "new",
                "name": "New",
                "description": "",
                "parent": self.made["security"].pk,
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("parent", form.errors)


class RetireActionTests(SubjectAdminTestCase):
    def post_action(self, action, *subjects):
        return self.client.post(
            reverse("admin:subjects_subject_changelist"),
            {
                "action": action,
                "_selected_action": [str(s.pk) for s in subjects],
            },
            follow=True,
        )

    def test_retiring_a_parent_is_refused_and_says_why(self):
        response = self.post_action("retire_selected", self.made["messaging"])
        self.assertFalse(Subject.all_objects.get(slug="messaging").is_retired)
        self.assertContains(response, "still has")

    def test_retiring_the_subtree_takes_the_branch(self):
        self.post_action("retire_subtree_selected", self.made["messaging"])
        for slug in ("messaging", "email", "smtp"):
            self.assertTrue(Subject.all_objects.get(slug=slug).is_retired, slug)

    def test_retiring_a_leaf_still_works_through_the_plain_action(self):
        self.post_action("retire_selected", self.made["smtp"])
        self.assertTrue(Subject.all_objects.get(slug="smtp").is_retired)


class AssignmentAdminTests(SubjectAdminTestCase):
    def test_the_assignment_listing_shows_the_whole_path(self):
        # The column that catches a mis-filing: a curator who sees
        # messaging/email/pop3 beside a Diffie-Hellman RFC notices, one who sees
        # pop3 does not.
        response = self.client.get(
            reverse("admin:subjects_subjectassignment_changelist")
        )
        self.assertContains(response, "messaging/email/smtp")

    def test_the_filter_offers_the_roots_rather_than_every_subject(self):
        response = self.client.get(
            reverse("admin:subjects_subjectassignment_changelist")
        )
        choices = [
            choice["display"]
            for spec in response.context["cl"].filter_specs
            if isinstance(spec, RootSubjectFilter)
            for choice in spec.choices(response.context["cl"])
        ]
        self.assertIn("Messaging", choices)
        self.assertNotIn("Smtp", choices)

    def test_filtering_by_a_root_finds_the_whole_branch(self):
        response = self.client.get(
            reverse("admin:subjects_subjectassignment_changelist"),
            {"root": "messaging"},
        )
        self.assertEqual(
            [a.doc for a in response.context["cl"].result_list], ["rfc5321"]
        )

    def test_a_root_filter_does_not_sweep_in_a_longer_sibling_slug(self):
        SubjectAssignment.objects.create(
            subject=self.made["security-and-privacy"], doc="rfc8446"
        )
        response = self.client.get(
            reverse("admin:subjects_subjectassignment_changelist"),
            {"root": "security"},
        )
        self.assertEqual([a.doc for a in response.context["cl"].result_list], [])
