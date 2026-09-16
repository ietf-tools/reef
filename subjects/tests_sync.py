# Copyright The IETF Trust 2026, All Rights Reserved
"""Mirroring the vocabulary and assignments from rfc-editor/rfc-subject-tags.

Fetches are always stubbed -- see RunSyncTestCase.run -- so nothing here touches
the network, matching precomputer/tests.py's own PrecomputeTestCase pattern.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from subscriptions.models import Subscription

from .models import Subject, SubjectAssignment
from .sync import (
    diff_assignments,
    diff_vocabulary,
    run_sync,
    suggest_merges,
    validate_taxonomy,
)
from .tests_hierarchy import tree

User = get_user_model()


def taxonomy(*entries):
    """A minimal parsed taxonomy.yaml from (id, parent, desc) tuples."""
    return {
        "tags": [
            {"id": tag_id, "parent": parent, "kind": "topic", "desc": desc}
            for tag_id, parent, desc in entries
        ]
    }


class ValidateTaxonomyTests(TestCase):
    def test_a_clean_taxonomy_has_no_problems(self):
        t = taxonomy(("security", None, "Security"), ("tls", "security", "TLS"))
        self.assertEqual(validate_taxonomy(t), [])

    def test_an_unknown_parent_is_a_problem(self):
        t = taxonomy(("tls", "security", "TLS"))
        problems = validate_taxonomy(t)
        self.assertTrue(any("unknown parent" in problem for problem in problems))

    def test_a_duplicate_id_is_a_problem(self):
        t = taxonomy(("security", None, "a"), ("security", None, "b"))
        problems = validate_taxonomy(t)
        self.assertTrue(
            any("appears more than once" in problem for problem in problems)
        )

    def test_a_catch_all_id_is_a_problem(self):
        t = taxonomy(("misc", None, "Misc"))
        problems = validate_taxonomy(t)
        self.assertTrue(any("catch-all" in problem for problem in problems))

    def test_a_parent_cycle_is_a_problem(self):
        t = taxonomy(("a", "b", "A"), ("b", "a", "B"))
        problems = validate_taxonomy(t)
        self.assertTrue(any("cycle" in problem for problem in problems))

    def test_a_path_deeper_than_four_levels_is_a_problem(self):
        t = taxonomy(
            ("l1", None, "1"),
            ("l2", "l1", "2"),
            ("l3", "l2", "3"),
            ("l4", "l3", "4"),
            ("l5", "l4", "5"),
        )
        problems = validate_taxonomy(t)
        self.assertTrue(any("deeper than 4" in problem for problem in problems))


class DiffVocabularyTests(TestCase):
    def test_a_new_tag_is_a_create(self):
        diff = diff_vocabulary(taxonomy(("security", None, "Security")))
        self.assertEqual(
            diff.to_create,
            [{"slug": "security", "parent_slug": None, "description": "Security"}],
        )

    def test_a_changed_description_is_an_update(self):
        Subject.objects.create(slug="security", name="Security", description="old")
        diff = diff_vocabulary(taxonomy(("security", None, "new")))
        self.assertEqual(len(diff.to_update), 1)
        self.assertEqual(diff.to_update[0].changes["description"], ("old", "new"))

    def test_reparenting_is_an_update(self):
        made = tree("a", "b")
        Subject.objects.create(slug="c", name="C", parent=made["a"])
        diff = diff_vocabulary(
            taxonomy(("a", None, ""), ("b", None, ""), ("c", "b", ""))
        )
        change = next(c for c in diff.to_update if c.slug == "c")
        self.assertEqual(change.changes["parent"], ("a", "b"))

    def test_an_unchanged_tag_is_neither_created_nor_updated(self):
        Subject.objects.create(slug="security", name="Security", description="Security")
        diff = diff_vocabulary(taxonomy(("security", None, "Security")))
        self.assertEqual(diff.to_create, [])
        self.assertEqual(diff.to_update, [])

    def test_a_vanished_live_subject_is_retired(self):
        Subject.objects.create(slug="old", name="Old")
        diff = diff_vocabulary(taxonomy())
        self.assertEqual([subject.slug for subject in diff.to_retire], ["old"])

    def test_an_already_retired_subject_is_not_retired_again(self):
        subject = Subject.objects.create(slug="old", name="Old")
        subject.retire()
        diff = diff_vocabulary(taxonomy())
        self.assertEqual(diff.to_retire, [])

    def test_a_retired_subject_reappearing_is_unretired(self):
        subject = Subject.objects.create(slug="security", name="Security")
        subject.retire()
        diff = diff_vocabulary(taxonomy(("security", None, "")))
        self.assertEqual(diff.to_unretire, ["security"])


class DiffAssignmentsTests(TestCase):
    def test_a_new_pair_is_a_create(self):
        subject = Subject.objects.create(slug="security", name="Security")
        diff = diff_assignments({"RFC9110": {"tags": ["security"]}})
        self.assertEqual(diff.to_create, [(subject.pk, "rfc9110")])
        self.assertEqual(diff.to_delete_pks, [])

    def test_an_existing_pair_is_kept(self):
        subject = Subject.objects.create(slug="security", name="Security")
        SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        diff = diff_assignments({"RFC9110": {"tags": ["security"]}})
        self.assertEqual(diff.to_create, [])
        self.assertEqual(diff.to_delete_pks, [])

    def test_a_vanished_pair_is_deleted(self):
        subject = Subject.objects.create(slug="security", name="Security")
        assignment = SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        diff = diff_assignments({})
        self.assertEqual(diff.to_delete_pks, [assignment.pk])

    def test_an_unknown_slug_is_unresolved_not_raised(self):
        diff = diff_assignments({"RFC9110": {"tags": ["no-such-tag"]}})
        self.assertEqual(diff.unresolved, [("no-such-tag", "rfc9110")])
        self.assertEqual(diff.to_create, [])


class SuggestMergesTests(TestCase):
    def test_strong_document_overlap_is_suggested(self):
        target = Subject.objects.create(
            slug="dns-security-extensions", name="Dnssec", description=""
        )
        SubjectAssignment.objects.create(subject=target, doc="rfc4033")
        SubjectAssignment.objects.create(subject=target, doc="rfc4034")

        suggestions = suggest_merges(
            ["dnssec"],
            old_docs={"dnssec": {"rfc4033", "rfc4034"}},
            old_parent_slug={"dnssec": None},
            old_description={"dnssec": "old wording"},
        )
        self.assertEqual(len(suggestions["dnssec"]), 1)
        self.assertEqual(
            suggestions["dnssec"][0].target_slug, "dns-security-extensions"
        )
        self.assertEqual(suggestions["dnssec"][0].shared_count, 2)

    def test_a_strong_description_match_with_no_overlap_is_still_suggested(self):
        Subject.objects.create(
            slug="dnssec-v2",
            name="Dnssec V2",
            description="DNS Security Extensions signing and validation",
        )
        suggestions = suggest_merges(
            ["dnssec"],
            old_docs={"dnssec": set()},
            old_parent_slug={"dnssec": None},
            old_description={
                "dnssec": "DNS Security Extensions signing and validation"
            },
        )
        self.assertEqual(len(suggestions["dnssec"]), 1)
        self.assertEqual(suggestions["dnssec"][0].target_slug, "dnssec-v2")

    def test_no_overlap_and_dissimilar_text_suggests_nothing(self):
        Subject.objects.create(
            slug="unrelated", name="Unrelated", description="Something else entirely"
        )
        suggestions = suggest_merges(
            ["old-tag"],
            old_docs={"old-tag": {"rfc1"}},
            old_parent_slug={"old-tag": None},
            old_description={"old-tag": "An old topic about something"},
        )
        self.assertEqual(suggestions["old-tag"], [])


class RunSyncTestCase(TestCase):
    def run_sync(self, taxonomy_data, rfc_tags_data=None, **kwargs):
        with (
            mock.patch("subjects.sync.fetch_taxonomy", return_value=taxonomy_data),
            mock.patch(
                "subjects.sync.fetch_assignments", return_value=rfc_tags_data or {}
            ),
        ):
            return run_sync(**kwargs)


class WriteTests(RunSyncTestCase):
    def test_dry_run_writes_nothing(self):
        result = self.run_sync(taxonomy(("security", None, "Security")), write=False)
        self.assertFalse(result.written)
        self.assertEqual(result.created, ["security"])
        self.assertEqual(Subject.all_objects.count(), 0)

    def test_write_creates_updates_and_retires(self):
        Subject.objects.create(slug="old-tag", name="Old", description="")
        result = self.run_sync(
            taxonomy(("security", None, "Security")),
            {"RFC9110": {"tags": ["security"]}},
        )
        self.assertTrue(result.written)
        self.assertEqual(Subject.objects.get(slug="security").description, "Security")
        self.assertTrue(Subject.all_objects.get(slug="old-tag").is_retired)
        self.assertEqual(
            SubjectAssignment.objects.filter(
                subject__slug="security", doc="rfc9110"
            ).count(),
            1,
        )

    def test_a_retiring_subject_with_live_children_retires_the_whole_branch(self):
        made = tree("messaging", "messaging/email")
        result = self.run_sync(taxonomy())
        self.assertTrue(result.written)
        made["messaging"].refresh_from_db()
        made["email"].refresh_from_db()
        self.assertTrue(made["messaging"].is_retired)
        self.assertTrue(made["email"].is_retired)

    def test_a_live_subscriber_keeps_matching_after_retirement(self):
        subject = Subject.objects.create(slug="old-tag", name="Old")
        user = User.objects.create(username="reader", oidc_sub="reader")
        subscription = Subscription.objects.create(
            user=user, kind=Subscription.Kind.SUBJECT, subject=subject
        )
        result = self.run_sync(taxonomy())
        self.assertTrue(result.written)
        subscription.refresh_from_db()
        self.assertEqual(subscription.subject_id, subject.pk)
        self.assertTrue(Subject.all_objects.get(pk=subject.pk).is_retired)

    def test_running_twice_with_nothing_changed_writes_nothing_the_second_time(self):
        t = taxonomy(("security", None, "Security"))
        rfc_tags = {"RFC9110": {"tags": ["security"]}}
        first = self.run_sync(t, rfc_tags)
        self.assertTrue(first.written)
        second = self.run_sync(t, rfc_tags)
        self.assertTrue(second.written)
        self.assertEqual(second.created, [])
        self.assertEqual(second.updated, [])
        self.assertEqual(second.retired, [])
        self.assertEqual(second.assignments_created, 0)
        self.assertEqual(second.assignments_deleted, 0)


class SafetyThresholdTests(RunSyncTestCase):
    def setUp(self):
        for index in range(10):
            Subject.objects.create(slug=f"tag-{index}", name=f"Tag {index}")

    def test_retiring_most_of_the_vocabulary_needs_confirmation(self):
        result = self.run_sync(taxonomy(("tag-0", None, "")))
        self.assertFalse(result.written)
        self.assertTrue(result.needs_confirmation)
        self.assertEqual(Subject.objects.filter(slug__startswith="tag-").count(), 10)

    def test_confirming_applies_it(self):
        result = self.run_sync(taxonomy(("tag-0", None, "")), confirm_large_change=True)
        self.assertTrue(result.written)
        self.assertEqual(Subject.objects.filter(slug__startswith="tag-").count(), 1)


class ConcurrentRunTests(RunSyncTestCase):
    def test_a_concurrent_run_is_reported_rather_than_started(self):
        with mock.patch("subjects.sync.advisory_lock") as lock:
            lock.return_value.__enter__.return_value = False
            result = self.run_sync(taxonomy(("security", None, "Security")))
        self.assertTrue(result.skipped)
        self.assertEqual(Subject.all_objects.count(), 0)


class SyncAdminViewTests(TestCase):
    def setUp(self):
        self.url = reverse("admin:subjects_subject_sync")
        self.staff = User.objects.create_superuser(
            username="staff", oidc_sub="staff", password="x"
        )

    def test_anonymous_is_sent_to_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login/", resp["Location"])

    def test_non_staff_is_sent_to_login(self):
        user = User.objects.create(username="plain", oidc_sub="plain", is_staff=False)
        self.client.force_login(user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)

    def test_staff_get_shows_the_form_and_no_result(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["result"])

    def test_staff_post_runs_the_sync(self):
        self.client.force_login(self.staff)
        with (
            mock.patch(
                "subjects.admin.run_sync",
                return_value=mock.Mock(
                    skipped=False,
                    validation_problems=[],
                    needs_confirmation=False,
                    written=True,
                    created=["security"],
                    updated=[],
                    unretired=[],
                    retired=[],
                    assignments_created=0,
                    assignments_deleted=0,
                    unresolved_assignments=[],
                    top_deltas=[],
                    suggestions={},
                ),
            ) as run,
        ):
            resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(run.called)
        self.assertContains(resp, "security")


class SyncMergeViewTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(
            username="staff", oidc_sub="staff", password="x"
        )
        self.client.force_login(self.staff)
        self.source = Subject.objects.create(slug="old-tag", name="Old")
        self.source.retire()
        self.target = Subject.objects.create(slug="new-tag", name="New")

    def url(self, source=None, target=None):
        return reverse(
            "admin:subjects_subject_sync_merge",
            kwargs={
                "source_id": (source or self.source).pk,
                "target_id": (target or self.target).pk,
            },
        )

    def test_get_is_not_allowed(self):
        resp = self.client.get(self.url())
        self.assertEqual(resp.status_code, 405)

    def test_post_unretires_and_merges(self):
        resp = self.client.post(self.url())
        self.assertEqual(resp.status_code, 302)
        self.source.refresh_from_db()
        self.assertTrue(self.source.is_retired)  # merge_subjects() retires it again
        self.assertEqual(self.source.merged_into_id, self.target.pk)

    def test_a_second_click_is_harmless(self):
        self.client.post(self.url())
        resp = self.client.post(self.url())
        self.assertEqual(resp.status_code, 302)

    def test_merging_into_a_retired_target_is_refused_with_a_message(self):
        self.target.retire()
        resp = self.client.post(self.url())
        self.assertEqual(resp.status_code, 302)
        self.source.refresh_from_db()
        self.assertIsNone(self.source.merged_into_id)
