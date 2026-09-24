# Copyright The IETF Trust 2026, All Rights Reserved
"""Mirroring the vocabulary and assignments from rfc-editor/rfc-subject-tags.

Fetches are always stubbed -- see RunSyncTestCase.run -- so nothing here touches
the network, matching precomputer/tests.py's own PrecomputeTestCase pattern.
"""

import logging
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.text import slugify

from subscriptions.models import Subscription

from .models import Subject, SubjectAlias, SubjectAssignment, SubjectSyncRun
from .sync import (
    SyncResult,
    diff_assignments,
    diff_vocabulary,
    run_sync,
    suggest_merges,
    validate_taxonomy,
)
from .tasks import run_subject_sync
from .tests_hierarchy import tree

User = get_user_model()


def tag_uuid(tag_id):
    """The uuid taxonomy() gives tag_id, stable across calls as upstream's is."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, tag_id))


def taxonomy(*entries):
    """A minimal parsed taxonomy.yaml from (id, parent, desc) tuples.

    A fourth element, a dict, overrides fields of that entry -- how a test
    renames a tag while keeping its uuid, or gives it a slug of its own.
    """
    tags = []
    for tag_id, parent, desc, *overrides in entries:
        entry = {
            "id": tag_id,
            "uuid": tag_uuid(tag_id),
            "slug": slugify(tag_id),
            "parent": parent,
            "kind": "topic",
            "desc": desc,
        }
        for override in overrides:
            entry.update(override)
        tags.append(entry)
    return {"tags": tags}


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

    def test_a_missing_or_malformed_uuid_is_a_problem(self):
        t = taxonomy(("a", None, "", {"uuid": None}), ("b", None, "", {"uuid": "x"}))
        problems = validate_taxonomy(t)
        self.assertEqual(sum("no valid uuid" in problem for problem in problems), 2)

    def test_a_shared_uuid_is_a_problem(self):
        t = taxonomy(("a", None, ""), ("b", None, "", {"uuid": tag_uuid("a")}))
        problems = validate_taxonomy(t)
        self.assertTrue(any("same uuid" in problem for problem in problems))

    def test_a_missing_or_malformed_slug_is_a_problem(self):
        t = taxonomy(
            ("a", None, "", {"slug": None}), ("HTTP/2", None, "", {"slug": "HTTP/2"})
        )
        problems = validate_taxonomy(t)
        self.assertEqual(sum("no valid slug" in problem for problem in problems), 2)

    def test_a_shared_slug_is_a_problem(self):
        t = taxonomy(("WHOIS", None, ""), ("WHOIS++", None, "", {"slug": "whois"}))
        problems = validate_taxonomy(t)
        self.assertTrue(any("same slug" in problem for problem in problems))

    def test_a_number_as_an_id_is_a_problem(self):
        t = taxonomy(("a", None, ""))
        t["tags"][0]["id"] = 802.11
        problems = validate_taxonomy(t)
        self.assertTrue(any("not a string" in problem for problem in problems))

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
            [
                {
                    "uuid": tag_uuid("security"),
                    "slug": "security",
                    "name": "security",
                    "parent_uuid": None,
                    "description": "Security",
                }
            ],
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
        Subject.objects.create(
            slug="security",
            name="security",
            description="Security",
            upstream_uuid=tag_uuid("security"),
        )
        diff = diff_vocabulary(taxonomy(("security", None, "Security")))
        self.assertEqual(diff.to_create, [])
        self.assertEqual(diff.to_update, [])

    def test_an_unlinked_subject_is_linked_by_slug(self):
        Subject.objects.create(slug="dns", name="DNS", description="DNS")
        diff = diff_vocabulary(taxonomy(("DNS", None, "DNS")))
        self.assertEqual(diff.to_create, [])
        self.assertEqual(
            diff.to_update[0].changes, {"upstream_uuid": (None, tag_uuid("DNS"))}
        )

    def test_an_unlinked_subject_is_linked_by_name_when_the_slug_differs(self):
        Subject.objects.create(slug="s-mime", name="S/MIME")
        diff = diff_vocabulary(taxonomy(("S/MIME", None, "")))
        self.assertEqual(diff.to_create, [])
        self.assertEqual(diff.to_update[0].changes["slug"], ("s-mime", "smime"))

    def test_a_linked_subject_is_matched_by_uuid_not_slug(self):
        """A subject another tag's slug happens to match is not taken from the
        tag whose uuid it carries."""
        Subject.objects.create(
            slug="whois", name="WHOIS++", upstream_uuid=tag_uuid("WHOIS++")
        )
        diff = diff_vocabulary(taxonomy(("WHOIS++", None, "", {"slug": "whois-2"})))
        self.assertEqual(diff.to_create, [])
        self.assertEqual(diff.to_update[0].changes["slug"], ("whois", "whois-2"))

    def test_a_created_subject_is_named_by_the_id(self):
        diff = diff_vocabulary(taxonomy(("HTTP/2", None, "")))
        self.assertEqual(diff.to_create[0]["slug"], "http2")
        self.assertEqual(diff.to_create[0]["name"], "HTTP/2")

    def test_an_existing_name_follows_the_id(self):
        Subject.objects.create(
            slug="doh", name="DNS over HTTPS", upstream_uuid=tag_uuid("DoH")
        )
        diff = diff_vocabulary(taxonomy(("DoH", None, "")))
        self.assertEqual(diff.to_update[0].changes, {"name": ("DNS over HTTPS", "DoH")})

    def test_a_name_held_outside_the_vocabulary_is_taken_from_that_subject(self):
        # Linked to a tag that has gone, so the name pass cannot match it either.
        Subject.objects.create(
            slug="kerberos-v4", name="Kerberos", upstream_uuid=tag_uuid("gone")
        )
        diff = diff_vocabulary(taxonomy(("Kerberos", None, "")))
        self.assertEqual(diff.to_create[0]["name"], "Kerberos")
        self.assertEqual(
            diff.displaced[0].changes,
            {"name": ("Kerberos", "Kerberos (kerberos-v4)")},
        )

    def test_a_name_held_by_another_matched_subject_displaces_nothing(self):
        """That subject is taking its own tag's id in the same write."""
        Subject.objects.create(
            slug="unrelated", name="DoH", upstream_uuid=tag_uuid("unrelated")
        )
        diff = diff_vocabulary(taxonomy(("unrelated", None, ""), ("DoH", None, "")))
        self.assertEqual(diff.displaced, [])
        self.assertEqual(diff.to_create[0]["name"], "DoH")

    def test_a_slug_another_subject_holds_is_a_conflict(self):
        Subject.objects.create(
            slug="whois", name="WHOIS", upstream_uuid=tag_uuid("WHOIS")
        )
        Subject.objects.create(
            slug="whois-2", name="WHOIS++", upstream_uuid=tag_uuid("WHOIS++")
        )
        # The file reordered, so upstream's numbering swapped the two.
        diff = diff_vocabulary(
            taxonomy(
                ("WHOIS++", None, "", {"slug": "whois"}),
                ("WHOIS", None, "", {"slug": "whois-2"}),
            )
        )
        self.assertEqual(len(diff.conflicts), 2)

    def test_a_slug_another_subjects_alias_holds_is_a_conflict(self):
        other = Subject.objects.create(slug="kerberos", name="Kerberos")
        SubjectAlias.objects.create(slug="krb5", subject=other)
        diff = diff_vocabulary(taxonomy(("Kerberos", None, ""), ("krb5", None, "")))
        self.assertEqual(len(diff.conflicts), 1)
        self.assertIn("krb5", diff.conflicts[0])

    def test_a_parent_renamed_in_the_same_run_is_not_a_reparent(self):
        made = tree("messaging", "messaging/email")
        for subject in made.values():
            subject.upstream_uuid = tag_uuid(subject.slug)
            subject.name = subject.slug
            subject.save()
        diff = diff_vocabulary(
            taxonomy(
                ("messaging", None, "", {"slug": "mail-and-messaging"}),
                ("email", "messaging", ""),
            )
        )
        self.assertEqual(
            [(change.slug, list(change.changes)) for change in diff.to_update],
            [("mail-and-messaging", ["slug"])],
        )

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
    def setUp(self):
        self.taxonomy = taxonomy(("security", None, ""))
        self.subject = Subject.objects.create(
            slug="security", name="Security", upstream_uuid=tag_uuid("security")
        )

    def test_a_new_pair_is_a_create(self):
        diff = diff_assignments({"RFC9110": {"tags": ["security"]}}, self.taxonomy)
        self.assertEqual(diff.to_create, [(self.subject.pk, "rfc9110")])
        self.assertEqual(diff.to_delete_pks, [])

    def test_an_existing_pair_is_kept(self):
        SubjectAssignment.objects.create(subject=self.subject, doc="rfc9110")
        diff = diff_assignments({"RFC9110": {"tags": ["security"]}}, self.taxonomy)
        self.assertEqual(diff.to_create, [])
        self.assertEqual(diff.to_delete_pks, [])

    def test_a_vanished_pair_is_deleted(self):
        assignment = SubjectAssignment.objects.create(
            subject=self.subject, doc="rfc9110"
        )
        diff = diff_assignments({}, self.taxonomy)
        self.assertEqual(diff.to_delete_pks, [assignment.pk])

    def test_an_unknown_id_is_unresolved_not_raised(self):
        diff = diff_assignments({"RFC9110": {"tags": ["no such tag"]}}, self.taxonomy)
        self.assertEqual(diff.unresolved, [("no such tag", "rfc9110")])
        self.assertEqual(diff.to_create, [])

    def test_an_id_is_resolved_through_its_uuid_not_as_a_slug(self):
        t = taxonomy(("HTTP/2", None, ""))
        subject = Subject.objects.create(
            slug="http2", name="HTTP/2", upstream_uuid=tag_uuid("HTTP/2")
        )
        diff = diff_assignments({"RFC9113": {"tags": ["HTTP/2"]}}, t)
        self.assertEqual(diff.to_create, [(subject.pk, "rfc9113")])


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

    def test_an_upstream_rename_renames_the_subject_and_keeps_its_subscribers(self):
        subject = Subject.objects.create(
            slug="x509", name="X.509", upstream_uuid=tag_uuid("X509")
        )
        SubjectAssignment.objects.create(subject=subject, doc="rfc5280")
        user = User.objects.create(username="reader", oidc_sub="reader")
        subscription = Subscription.objects.create(
            user=user, kind=Subscription.Kind.SUBJECT, subject=subject
        )
        result = self.run_sync(
            taxonomy(("X.509", None, "", {"uuid": tag_uuid("X509"), "slug": "x-509"})),
            {"RFC5280": {"tags": ["X.509"]}},
        )
        self.assertTrue(result.written)
        self.assertEqual(result.retired, [])
        subject.refresh_from_db()
        self.assertEqual(subject.slug, "x-509")
        self.assertFalse(subject.is_retired)
        self.assertTrue(
            SubjectAlias.objects.filter(slug="x509", subject=subject).exists()
        )
        self.assertEqual(result.assignments_created, 0)
        self.assertEqual(result.assignments_deleted, 0)
        subscription.refresh_from_db()
        self.assertEqual(subscription.subject_id, subject.pk)

    def test_an_id_with_a_slash_never_reaches_the_path(self):
        result = self.run_sync(
            taxonomy(("web", None, ""), ("HTTP/2", "web", "")),
            {"RFC9113": {"tags": ["HTTP/2"]}},
        )
        self.assertTrue(result.written)
        subject = Subject.objects.get(upstream_uuid=tag_uuid("HTTP/2"))
        self.assertEqual(subject.path, "web/http2")
        self.assertEqual(subject.name, "HTTP/2")
        self.assertTrue(
            SubjectAssignment.objects.filter(subject=subject, doc="rfc9113").exists()
        )

    def test_two_subjects_trading_names_are_both_written(self):
        first = Subject.objects.create(
            slug="a", name="Beta", upstream_uuid=tag_uuid("Alpha")
        )
        second = Subject.objects.create(
            slug="b", name="Alpha", upstream_uuid=tag_uuid("Beta")
        )
        result = self.run_sync(
            taxonomy(
                ("Alpha", None, "", {"slug": "a"}), ("Beta", None, "", {"slug": "b"})
            )
        )
        self.assertTrue(result.written)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.name, second.name), ("Alpha", "Beta"))

    def test_a_retired_subject_holding_a_name_gives_it_up(self):
        holder = Subject.objects.create(
            slug="kerberos-v4", name="Kerberos", upstream_uuid=tag_uuid("gone")
        )
        holder.retire()
        result = self.run_sync(taxonomy(("Kerberos", None, "")))
        self.assertTrue(result.written)
        holder.refresh_from_db()
        self.assertEqual(holder.name, "Kerberos (kerberos-v4)")
        self.assertTrue(holder.is_retired)
        self.assertEqual(Subject.objects.get(slug="kerberos").name, "Kerberos")

    def test_a_slug_conflict_writes_nothing(self):
        Subject.objects.create(
            slug="taken", name="Taken", upstream_uuid=tag_uuid("vanished")
        )
        result = self.run_sync(
            taxonomy(("new", None, "", {"slug": "taken"})),
            {"RFC1": {"tags": ["new"]}},
            confirm_large_change=True,
        )
        self.assertFalse(result.written)
        self.assertEqual(len(result.validation_problems), 1)
        self.assertFalse(Subject.all_objects.get(slug="taken").is_retired)
        self.assertEqual(SubjectAssignment.objects.count(), 0)

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
    """The button only ever creates a SubjectSyncRun and hands it to Celery --
    see plan.md for why a request-bound sync isn't safe."""

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

    def test_staff_get_shows_the_form_and_recent_runs(self):
        self.client.force_login(self.staff)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sync now")

    def test_staff_post_creates_a_run_and_enqueues_it_without_blocking(self):
        self.client.force_login(self.staff)
        with mock.patch("subjects.admin.run_subject_sync.delay") as delay:
            resp = self.client.post(self.url)
        run = SubjectSyncRun.objects.get()
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"/sync/runs/{run.pk}/", resp["Location"])
        self.assertEqual(run.status, SubjectSyncRun.Status.PENDING)
        self.assertEqual(run.triggered_by, self.staff)
        delay.assert_called_once_with(run.pk)

    def test_confirm_large_change_is_recorded_on_the_run(self):
        self.client.force_login(self.staff)
        with mock.patch("subjects.admin.run_subject_sync.delay"):
            self.client.post(self.url, {"confirm_large_change": "1"})
        run = SubjectSyncRun.objects.get()
        self.assertTrue(run.confirm_large_change)


class SyncRunViewTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(
            username="staff", oidc_sub="staff", password="x"
        )
        self.client.force_login(self.staff)

    def url(self, run):
        return reverse("admin:subjects_subject_sync_run", kwargs={"run_id": run.pk})

    def test_a_pending_run_shows_the_polling_page(self):
        run = SubjectSyncRun.objects.create()
        resp = self.client.get(self.url(run))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "refresh")

    def test_a_succeeded_run_shows_its_result(self):
        run = SubjectSyncRun.objects.create(
            status=SubjectSyncRun.Status.SUCCEEDED,
            result={
                "written": True,
                "created": ["security"],
                "updated": [],
                "unretired": [],
                "retired": [],
                "assignments_created": 0,
                "assignments_deleted": 0,
                "unresolved_assignments": [],
                "top_deltas": [],
                "suggestions": {},
                "validation_problems": [],
            },
        )
        resp = self.client.get(self.url(run))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "security")

    def test_a_retired_subject_in_the_result_offers_its_merge_suggestion(self):
        target = Subject.objects.create(slug="new-tag", name="New Tag")
        run = SubjectSyncRun.objects.create(
            status=SubjectSyncRun.Status.SUCCEEDED,
            result={
                "written": True,
                "created": [],
                "updated": [],
                "unretired": [],
                "retired": ["old-tag"],
                "assignments_created": 0,
                "assignments_deleted": 0,
                "unresolved_assignments": [],
                "top_deltas": [],
                "suggestions": {
                    "old-tag": [
                        {
                            "target_slug": "new-tag",
                            "target_name": "New Tag",
                            "target_pk": target.pk,
                            "score": 0.8,
                            "jaccard": 0.7,
                            "shared_count": 8,
                            "old_count": 10,
                            "description_ratio": 0.5,
                        }
                    ]
                },
                "validation_problems": [],
            },
        )
        source = Subject.objects.create(slug="old-tag", name="Old Tag")
        source.retire()
        resp = self.client.get(self.url(run))
        self.assertContains(resp, "Merge into this")
        self.assertContains(resp, f"/sync/merge/{source.pk}/{target.pk}/")

    def test_confirming_a_needs_confirmation_run_creates_a_fresh_confirmed_run(self):
        run = SubjectSyncRun.objects.create(
            status=SubjectSyncRun.Status.NEEDS_CONFIRMATION,
            result={
                "retire_count": 9,
                "live_count_before": 10,
                "assignment_total_before": 100,
                "assignment_total_after": 40,
                "validation_problems": [],
            },
        )
        with mock.patch("subjects.admin.run_subject_sync.delay") as delay:
            resp = self.client.post(self.url(run))
        self.assertEqual(SubjectSyncRun.objects.count(), 2)
        confirmed = SubjectSyncRun.objects.exclude(pk=run.pk).get()
        self.assertTrue(confirmed.confirm_large_change)
        self.assertIn(f"/sync/runs/{confirmed.pk}/", resp["Location"])
        delay.assert_called_once_with(confirmed.pk)
        # The original stays exactly what the safety threshold saw.
        run.refresh_from_db()
        self.assertEqual(run.status, SubjectSyncRun.Status.NEEDS_CONFIRMATION)


class RunSubjectSyncTaskTests(TestCase):
    """subjects.tasks.run_subject_sync, with subjects.sync.run_sync itself
    stubbed -- its own behaviour is RunSyncTestCase's job."""

    def _run(self, **result_kwargs):
        run = SubjectSyncRun.objects.create()
        with mock.patch(
            "subjects.tasks.run_sync", return_value=SyncResult(**result_kwargs)
        ) as run_sync_mock:
            run_subject_sync(run.pk)
        run.refresh_from_db()
        return run, run_sync_mock

    def test_a_missing_run_is_logged_not_raised(self):
        run_subject_sync(999999)  # must not raise

    def test_a_successful_run_is_marked_succeeded_with_its_result_stored(self):
        run, _ = self._run(created=["security"])
        self.assertEqual(run.status, SubjectSyncRun.Status.SUCCEEDED)
        self.assertEqual(run.result["created"], ["security"])
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.finished_at)

    def test_a_skipped_run_is_marked_skipped(self):
        run, _ = self._run(skipped=True, written=False)
        self.assertEqual(run.status, SubjectSyncRun.Status.SKIPPED)

    def test_validation_problems_mark_the_run_failed(self):
        run, _ = self._run(validation_problems=["bad"], written=False)
        self.assertEqual(run.status, SubjectSyncRun.Status.FAILED)

    def test_needing_confirmation_is_its_own_status(self):
        run, _ = self._run(needs_confirmation=True, written=False)
        self.assertEqual(run.status, SubjectSyncRun.Status.NEEDS_CONFIRMATION)

    def test_an_exception_marks_the_run_failed_with_its_traceback_recorded(self):
        run = SubjectSyncRun.objects.create()
        with mock.patch("subjects.tasks.run_sync", side_effect=RuntimeError("boom")):
            run_subject_sync(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, SubjectSyncRun.Status.FAILED)
        self.assertIn("RuntimeError: boom", run.error)
        # The traceback, not just the message -- this is a staff-only page,
        # so there's no reason debugging a failure should need log access.
        self.assertIn("Traceback (most recent call last)", run.error)
        self.assertIsNone(run.result)

    def test_a_subject_sync_log_line_is_mirrored_onto_progress_message(self):
        """The handler reuses subjects/sync.py's own checkpoint logging --
        see _ProgressHandler's docstring -- rather than a second reporting
        path, so this simulates one of those checkpoints firing mid-run."""
        run = SubjectSyncRun.objects.create()

        def fake_run_sync(**kwargs):
            logging.getLogger("reef").info("subject sync: created 3/542: dnssec")
            return SyncResult(written=True)

        with mock.patch("subjects.tasks.run_sync", side_effect=fake_run_sync):
            run_subject_sync(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.progress_message, "subject sync: created 3/542: dnssec")

    def test_an_unrelated_log_line_is_not_mirrored(self):
        run = SubjectSyncRun.objects.create()

        def fake_run_sync(**kwargs):
            logging.getLogger("reef").info("something unrelated entirely")
            return SyncResult(written=True)

        with mock.patch("subjects.tasks.run_sync", side_effect=fake_run_sync):
            run_subject_sync(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.progress_message, "")

    def test_the_handler_is_removed_after_the_run(self):
        """Otherwise a second run's progress would keep being written onto
        every earlier run's row too, since addHandler is cumulative."""
        run = SubjectSyncRun.objects.create()
        with mock.patch(
            "subjects.tasks.run_sync", return_value=SyncResult(written=True)
        ):
            run_subject_sync(run.pk)
        logging.getLogger("reef").info("subject sync: after the run entirely")
        run.refresh_from_db()
        self.assertNotEqual(
            run.progress_message, "subject sync: after the run entirely"
        )


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
