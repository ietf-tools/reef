# Copyright The IETF Trust 2026, All Rights Reserved
import json
from io import StringIO
from unittest import mock

from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from reef.testing import stub_rfc_index

from .compute import recompute_popularity, replace_ranking
from .matomo import parse_rankings, percentile
from .models import DocumentPopularity, MatomoImportRun, MatomoRanking
from .tasks import LOCK_MAX_RETRIES, publish_matomo_import

User = get_user_model()


def page(label, visits, *children):
    row = {"label": label, "nb_visits": visits, "nb_hits": visits * 3}
    if children:
        row["subtable"] = list(children)
    return row


def export(**directories):
    return [
        page(name, sum(c["nb_visits"] for c in rows), *rows)
        for name, rows in directories.items()
    ]


class PercentileTests(TestCase):
    def test_orders_from_zero_to_one(self):
        scores = percentile({"rfc1": 5, "rfc2": 50, "rfc3": 500})
        self.assertEqual(scores, {"rfc1": 0.0, "rfc2": 0.5, "rfc3": 1.0})

    def test_ties_share_a_score(self):
        scores = percentile({"rfc1": 5, "rfc2": 5, "rfc3": 500})
        self.assertEqual(scores, {"rfc1": 0.0, "rfc2": 0.0, "rfc3": 1.0})

    def test_a_lone_document_is_the_most_popular(self):
        self.assertEqual(percentile({"rfc1": 1}), {"rfc1": 1.0})

    def test_empty(self):
        self.assertEqual(percentile({}), {})


class ParseRankingsTests(TestCase):
    def test_sums_a_document_across_the_counted_directories(self):
        parsed = parse_rankings(
            export(
                info=[page("rfc9110", 100, page("/index", 90), page("/?utm=x", 10))],
                rfc=[
                    page("/rfc9110.txt", 5),
                    page("/rfc9110.html", 5),
                    page("/rfc2119", 200),
                ],
                pdfrfc=[page("/rfc9110.txt.pdf", 1)],
            )
        )
        self.assertEqual(parsed.scores, {"rfc9110": 0.0, "rfc2119": 1.0})
        self.assertEqual(parsed.rows_seen, 5)
        self.assertEqual(parsed.rows_ignored, 0)
        self.assertEqual(parsed.truncated, [])

    def test_a_folder_is_taken_at_its_own_visits_and_not_descended(self):
        parsed = parse_rankings(
            export(info=[page("rfc9110", 100, page("/index", 90)), page("rfc2119", 50)])
        )
        self.assertEqual(parsed.scores, {"rfc9110": 1.0, "rfc2119": 0.0})
        self.assertEqual(parsed.rows_seen, 2)

    def test_leading_zeros_and_case_resolve_to_one_document(self):
        parsed = parse_rankings(
            export(rfc=[page("/RFC0791.txt", 1), page("/rfc791", 1)])
        )
        self.assertEqual(parsed.scores, {"rfc791": 1.0})

    def test_other_series_and_directories_are_ignored_and_counted(self):
        parsed = parse_rankings(
            export(
                info=[page("rfc9110", 1), page("bcp14", 500), page("std1", 500)],
                doc=[page("rfc8446", 900)],
                prerelease=[page("/rfc8680.notprepped.xml", 900)],
                person=[page("/someone@example.com", 900)],
            )
        )
        self.assertEqual(parsed.scores, {"rfc9110": 1.0})
        self.assertEqual(parsed.rows_seen, 3)
        self.assertEqual(parsed.rows_ignored, 2)

    def test_an_others_row_marks_the_directory_truncated(self):
        parsed = parse_rankings(
            export(
                info=[page("rfc9110", 1), page("Others", 5000)],
                rfc=[page("/rfc9110", 1)],
            )
        )
        self.assertEqual(parsed.truncated, ["info"])
        self.assertEqual(parsed.scores, {"rfc9110": 1.0})

    def test_malformed_rows_are_ignored_rather_than_raised(self):
        parsed = parse_rankings(
            [
                {
                    "label": "info",
                    "subtable": [
                        {"nb_visits": 3},
                        "junk",
                        {"label": "rfc1", "nb_visits": "7"},
                    ],
                },
                "junk",
                {"label": "rfc"},
            ]
        )
        self.assertEqual(parsed.scores, {"rfc1": 1.0})
        self.assertEqual(parsed.rows_ignored, 2)

    def test_nothing_matching_gives_no_scores(self):
        self.assertEqual(parse_rankings({"not": "a list"}).scores, {})
        self.assertEqual(parse_rankings([]).scores, {})


class ReplaceRankingTests(TestCase):
    def test_upsert_keeps_created_at_and_moves_updated_at(self):
        replace_ranking(MatomoRanking, {"rfc1": 0.0, "rfc2": 1.0})
        before = MatomoRanking.objects.get(rfc="rfc1")
        replace_ranking(MatomoRanking, {"rfc1": 1.0})
        after = MatomoRanking.objects.get(rfc="rfc1")
        self.assertEqual(after.created_at, before.created_at)
        self.assertGreater(after.updated_at, before.updated_at)
        self.assertEqual(after.score, 1.0)

    def test_documents_absent_from_the_new_ranking_are_deleted(self):
        replace_ranking(MatomoRanking, {"rfc1": 0.0, "rfc2": 1.0})
        deleted = replace_ranking(MatomoRanking, {"rfc2": 1.0})
        self.assertEqual(deleted, 1)
        self.assertEqual(
            list(MatomoRanking.objects.values_list("rfc", flat=True)), ["rfc2"]
        )


class RecomputeTests(TestCase):
    def test_popularity_follows_the_matomo_ranking(self):
        replace_ranking(MatomoRanking, {"rfc1": 0.25, "rfc2": 1.0})
        result = recompute_popularity()
        self.assertEqual(result.ranked, 2)
        self.assertEqual(
            list(DocumentPopularity.objects.values_list("rfc", "score")),
            [("rfc2", 1.0), ("rfc1", 0.25)],
        )

    def test_a_document_no_source_ranks_is_removed(self):
        replace_ranking(MatomoRanking, {"rfc1": 1.0})
        recompute_popularity()
        replace_ranking(MatomoRanking, {"rfc2": 1.0})
        result = recompute_popularity()
        self.assertEqual(result.deleted, 1)
        self.assertEqual(
            list(DocumentPopularity.objects.values_list("rfc", flat=True)), ["rfc2"]
        )

    def test_sources_are_averaged(self):
        with mock.patch(
            "popularity.compute.SOURCES",
            (lambda: {"rfc1": 0.2, "rfc2": 1.0}, lambda: {"rfc1": 0.6}),
        ):
            recompute_popularity()
        scores = dict(DocumentPopularity.objects.values_list("rfc", "score"))
        self.assertAlmostEqual(scores["rfc1"], 0.4)
        self.assertEqual(scores["rfc2"], 1.0)

    def test_the_command_reports_the_counts(self):
        replace_ranking(MatomoRanking, {"rfc1": 1.0})
        out = StringIO()
        call_command("recompute_popularity", stdout=out)
        self.assertIn("Ranked 1 document(s), removed 0", out.getvalue())

    def test_identifiers_are_stored_canonically(self):
        row = MatomoRanking(
            rfc="RFC 0791",
            score=1.0,
            created_at=timezone.now(),
            updated_at=timezone.now(),
        )
        row.save()
        row.refresh_from_db()
        self.assertEqual(row.rfc, "rfc791")


class PopularityApiTests(APITestCase):
    def test_public_ranking_most_popular_first(self):
        replace_ranking(DocumentPopularity, {"rfc2": 0.0, "rfc1": 1.0, "rfc3": 0.5})
        resp = self.client.get("/api/reef/popularity/")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(
            body["entries"],
            [
                {"rfc": "rfc1", "popularity": 1.0},
                {"rfc": "rfc3", "popularity": 0.5},
                {"rfc": "rfc2", "popularity": 0.0},
            ],
        )
        latest = DocumentPopularity.objects.order_by("-updated_at").first().updated_at
        self.assertEqual(body["computed_at"], latest.isoformat().replace("+00:00", "Z"))

    def test_an_empty_ranking_has_no_computed_at(self):
        resp = self.client.get("/api/reef/popularity/")
        self.assertEqual(resp.json(), {"computed_at": None, "entries": []})


@override_settings(CELERY_TASK_ALWAYS_EAGER=False)
class ImportViewTests(TestCase):
    def setUp(self):
        self.url = reverse("admin:popularity")
        self.staff = User.objects.create(
            username="admin", oidc_sub="s-admin", is_staff=True
        )
        patcher = mock.patch("popularity.admin.publish_matomo_import.delay")
        self.delay = patcher.start()
        self.addCleanup(patcher.stop)
        stub_rfc_index(self)

    def upload(self, payload, **extra):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return self.client.post(
            self.url,
            {"export": SimpleUploadedFile("matomo.json", body, "application/json")},
            **extra,
        )

    def test_anonymous_is_sent_to_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login/", resp["Location"])

    def test_anonymous_post_is_not_parsed(self):
        with mock.patch("popularity.admin.parse_rankings") as parse:
            resp = self.upload(export(info=[page("rfc9110", 1)]))
        self.assertEqual(resp.status_code, 302)
        parse.assert_not_called()
        self.assertFalse(MatomoRanking.objects.exists())

    def test_non_staff_is_sent_to_login(self):
        self.client.force_login(
            User.objects.create(username="plain", oidc_sub="s-plain")
        )
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_staff_get_shows_the_form_and_recent_runs(self):
        MatomoImportRun.objects.create(triggered_by=self.staff, rfcs_ranked=3)
        self.client.force_login(self.staff)
        resp = self.client.get(self.url)
        self.assertContains(resp, "Matomo export")
        self.assertContains(resp, "Recent imports")

    def test_the_page_shadows_the_app_index_and_links_the_tables(self):
        self.staff.is_superuser = True
        self.staff.save()
        self.client.force_login(self.staff)
        resp = self.client.get("/admin/popularity/")
        self.assertContains(resp, "Matomo export")
        self.assertContains(resp, reverse("admin:popularity_matomoranking_changelist"))
        self.assertContains(
            resp, reverse("admin:popularity_documentpopularity_changelist")
        )

    def test_a_good_file_replaces_the_ranking_and_enqueues_one_publish(self):
        replace_ranking(MatomoRanking, {"rfc1": 1.0})
        self.client.force_login(self.staff)
        resp = self.upload(
            export(
                info=[page("rfc9110", 100), page("bcp14", 5), page("Others", 1)],
                rfc=[page("/rfc2119.txt", 1)],
            )
        )
        run = MatomoImportRun.objects.get()
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"/popularity/runs/{run.pk}/", resp["Location"])
        self.assertEqual(
            dict(MatomoRanking.objects.values_list("rfc", "score")),
            {"rfc9110": 1.0, "rfc2119": 0.0},
        )
        self.assertEqual(run.status, MatomoImportRun.Status.PENDING)
        self.assertEqual(run.triggered_by, self.staff)
        self.assertEqual((run.rows_seen, run.rfcs_ranked, run.rows_ignored), (4, 2, 2))
        self.assertEqual(run.truncated, ["info"])
        self.delay.assert_called_once_with(run.pk)
        # Nothing but ids and scores reaches the row, not even the ignored labels.
        for value in (run.output, run.error, run.progress_message):
            self.assertNotIn("bcp14", value)

    def test_a_file_naming_no_rfc_replaces_nothing(self):
        replace_ranking(MatomoRanking, {"rfc1": 1.0})
        self.client.force_login(self.staff)
        resp = self.upload(export(person=[page("/someone@example.com", 9)]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No RFC page was found")
        self.assertNotContains(resp, "someone@example.com")
        self.assertEqual(MatomoRanking.objects.get().rfc, "rfc1")
        self.assertFalse(MatomoImportRun.objects.exists())
        self.delay.assert_not_called()

    def test_invalid_json_is_a_form_error_by_position(self):
        self.client.force_login(self.staff)
        resp = self.upload(b'[{"label": "info", "secret@example.com')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Not valid JSON")
        self.assertNotContains(resp, "secret@example.com")
        self.delay.assert_not_called()

    def test_the_upload_never_touches_disk(self):
        """A file above Django's memory threshold still arrives as an in-memory
        upload, because only the memory handler is installed."""
        seen = {}

        def parse(payload):
            seen["payload"] = payload
            return mock.DEFAULT

        big = export(
            info=[page("rfc9110", 1)] + [page(f"/?pad={i}", 1) for i in range(1, 20000)]
        )
        self.client.force_login(self.staff)
        with (
            override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=1024),
            mock.patch(
                "popularity.admin.MatomoUploadForm.clean_export", autospec=True
            ) as clean,
        ):

            def clean_export(form):
                upload = form.cleaned_data["export"]
                seen["type"] = type(upload).__name__
                return parse_rankings(json.load(upload))

            clean.side_effect = clean_export
            self.upload(big)
        self.assertEqual(seen["type"], "InMemoryUploadedFile")

    def test_a_file_over_the_cap_is_refused(self):
        self.client.force_login(self.staff)
        with mock.patch("popularity.admin.MATOMO_UPLOAD_MAX_BYTES", 64):
            resp = self.upload(export(info=[page("rfc9110", 1), page("rfc2119", 1)]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "larger than the")
        self.assertFalse(MatomoRanking.objects.exists())
        self.delay.assert_not_called()

    def test_the_run_page_shows_counts_and_the_truncation_warning(self):
        run = MatomoImportRun.objects.create(
            status=MatomoImportRun.Status.RUNNING,
            rows_seen=10,
            rfcs_ranked=4,
            rows_ignored=6,
            truncated=["info", "rfc"],
            progress_message="[popularity] rendering",
        )
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("admin:popularity-run", args=[run.pk]))
        self.assertContains(resp, 'http-equiv="refresh"')
        self.assertContains(resp, "4 RFC(s) ranked")
        self.assertContains(resp, "info, rfc, so the ranking")
        self.assertContains(resp, "[popularity] rendering")

    def test_a_finished_run_stops_refreshing(self):
        run = MatomoImportRun.objects.create(
            status=MatomoImportRun.Status.FAILED, error="boom"
        )
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("admin:popularity-run", args=[run.pk]))
        self.assertNotContains(resp, 'http-equiv="refresh"')
        self.assertContains(resp, "boom")

    def test_the_rankings_are_read_only(self):
        self.staff.is_superuser = True
        self.staff.save()
        self.client.force_login(self.staff)
        for name in ("matomoranking", "documentpopularity"):
            self.assertEqual(
                self.client.get(reverse(f"admin:popularity_{name}_add")).status_code,
                403,
            )


class PublishTaskTests(TestCase):
    def setUp(self):
        replace_ranking(MatomoRanking, {"rfc1": 1.0})
        self.run = MatomoImportRun.objects.create(rfcs_ranked=1)
        patcher = mock.patch("popularity.tasks.call_command")
        self.call_command = patcher.start()
        self.addCleanup(patcher.stop)

    def test_recomputes_then_precomputes_only_popularity(self):
        publish_matomo_import(self.run.pk)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, MatomoImportRun.Status.SUCCEEDED)
        self.assertEqual(
            dict(DocumentPopularity.objects.values_list("rfc", "score")), {"rfc1": 1.0}
        )
        self.assertIn("Recomputed popularity for 1 document(s)", self.run.output)
        args, kwargs = self.call_command.call_args
        self.assertEqual(args, ("precompute", "popularity"))
        self.assertIsNotNone(self.run.finished_at)

    def test_a_failed_precompute_is_recorded_with_the_tables_updated(self):
        self.call_command.side_effect = CommandError("store down")
        publish_matomo_import(self.run.pk)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, MatomoImportRun.Status.FAILED)
        self.assertIn("store down", self.run.error)
        self.assertTrue(DocumentPopularity.objects.filter(rfc="rfc1").exists())

    def test_waits_for_a_run_holding_the_lock(self):
        with mock.patch("popularity.tasks.advisory_lock") as lock:
            lock.return_value.__enter__.return_value = False
            with self.assertRaises(Retry):
                publish_matomo_import.apply(args=[self.run.pk], throw=True)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, MatomoImportRun.Status.RUNNING)
        self.assertIn("Waiting", self.run.progress_message)
        self.call_command.assert_not_called()

    def test_gives_up_after_the_retries(self):
        with mock.patch("popularity.tasks.advisory_lock") as lock:
            lock.return_value.__enter__.return_value = False
            publish_matomo_import.apply(
                args=[self.run.pk], retries=LOCK_MAX_RETRIES, throw=True
            )
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, MatomoImportRun.Status.FAILED)
        self.assertIn("Gave up", self.run.error)

    def test_a_vanished_run_is_logged_and_skipped(self):
        self.run.delete()
        with self.assertLogs("reef", level="ERROR"):
            publish_matomo_import(self.run.pk)
        self.call_command.assert_not_called()
