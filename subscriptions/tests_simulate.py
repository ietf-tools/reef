# Copyright The IETF Trust 2026, All Rights Reserved
"""The admin change-detection simulator, which must never change anything but its
own run row."""

import datetime
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from docsets.models import DocumentSet, DocumentSetEntry
from reef import rfcmeta
from reef.testing import document_meta as meta
from reef.tests_rfcmeta import entry, index_payload
from reefauth.testing import login
from subscriptions import simulate as simulate_module
from subscriptions.changes import reduce_index, save_snapshot
from subscriptions.models import (
    DocumentSnapshot,
    PendingNotification,
    SimulationRun,
    SubjectNotificationEvent,
    Subscription,
    WebNotification,
)
from subscriptions.simulate import Phases, read_only_database, simulate
from subscriptions.tasks import run_simulation

User = get_user_model()

SNAPSHOT_DAY = datetime.date(2026, 8, 30)
INDEX_DAY = "2026-08-31"


def upload(payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return SimpleUploadedFile("rfc-index.json", body, "application/json")


class ReadOnlyDatabaseTests(TestCase):
    def test_reads_are_allowed(self):
        User.objects.create(username="u", oidc_sub="s")
        with read_only_database():
            self.assertEqual(User.objects.count(), 1)

    def test_a_write_is_refused_and_nothing_is_stored(self):
        with self.assertRaises(PermissionError):
            with read_only_database():
                User.objects.create(username="u", oidc_sub="s")
        self.assertEqual(User.objects.count(), 0)

    def test_every_kind_of_write_is_refused(self):
        user = User.objects.create(username="u", oidc_sub="s")
        for write in (
            lambda: User.objects.filter(pk=user.pk).update(email="x@example.org"),
            lambda: User.objects.filter(pk=user.pk).delete(),
            lambda: connection.cursor().execute(
                "DELETE FROM subscriptions_webnotification"
            ),
        ):
            with self.subTest():
                with self.assertRaises(PermissionError):
                    with read_only_database():
                        write()
        self.assertTrue(User.objects.filter(pk=user.pk, email="").exists())

    def test_whatever_gets_past_the_filter_is_rolled_back(self):
        """The filter is the first defence, not the only one."""
        with mock.patch.object(
            simulate_module,
            "_refuse_writes",
            side_effect=lambda execute, sql, params, many, context: execute(
                sql, params, many, context
            ),
        ):
            try:
                with read_only_database():
                    User.objects.create(username="u", oidc_sub="s")
            except DatabaseError:
                # PostgreSQL refuses the write itself.
                pass
        self.assertEqual(User.objects.count(), 0)

    def test_the_connection_is_usable_afterwards(self):
        with self.assertRaises(PermissionError):
            with read_only_database():
                User.objects.create(username="u", oidc_sub="s")
        User.objects.create(username="after", oidc_sub="s2")
        self.assertEqual(User.objects.count(), 1)


class SimulatorTestCase(TestCase):
    def setUp(self):
        rfcmeta.clear_cache()
        self.addCleanup(rfcmeta.clear_cache)
        self.url = reverse("admin:subscriptions-simulate")
        self.staff = User.objects.create(
            username="admin", oidc_sub="s-admin", is_staff=True, is_superuser=True
        )
        self.reader = User.objects.create(
            username="reader",
            oidc_sub="s-reader",
            email="reader@staff.ietf.org",
            receive_digest_email=True,
        )
        self.quiet = User.objects.create(
            username="quiet", oidc_sub="s-quiet", email="quiet@example.org"
        )
        Subscription.objects.create(user=self.reader, kind=Subscription.Kind.NEW_RFC)
        Subscription.objects.create(user=self.quiet, kind=Subscription.Kind.NEW_RFC)
        login(self.client, self.staff)

    def snapshot_of(self, mapping, day=SNAPSHOT_DAY):
        save_snapshot(reduce_index(mapping), day)

    def post(self, payload, holds=SimulationRun.Holds.INDEX):
        return self.client.post(self.url, {"holds": holds, "index": upload(payload)})

    def simulate(self, payload, holds=SimulationRun.Holds.INDEX):
        """Upload, run the queued task in place, and open the run's page."""
        with mock.patch(
            "subscriptions.admin.run_simulation.delay", side_effect=run_simulation
        ):
            posted = self.post(payload, holds)
        self.assertEqual(posted.status_code, 302)
        return self.client.get(posted["Location"])

    def latest_run(self):
        return SimulationRun.objects.latest("created_at")

    def two_documents(self, day=INDEX_DAY):
        return index_payload(
            [entry(), entry(number=9111, title="HTTP Caching")], created_on=day
        )


class SimulateViewTests(SimulatorTestCase):
    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_staff_without_permission_is_refused(self):
        run = SimulationRun.objects.create()
        plain = User.objects.create(username="p", oidc_sub="s-p", is_staff=True)
        login(self.client, plain)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        posted = self.post(self.two_documents())
        self.assertEqual(posted.status_code, 403)
        run_url = reverse("admin:subscriptions-simulate-run", args=[run.pk])
        self.assertEqual(self.client.get(run_url).status_code, 403)

    def test_get_shows_the_form(self):
        self.assertContains(self.client.get(self.url), "rfc-index.json")

    def test_an_upload_is_queued_and_redirects_to_its_run(self):
        with mock.patch("subscriptions.admin.run_simulation.delay") as delay:
            posted = self.post(self.two_documents())
        run = self.latest_run()
        delay.assert_called_once_with(run.pk)
        self.assertRedirects(
            posted,
            reverse("admin:subscriptions-simulate-run", args=[run.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(run.triggered_by, self.staff)
        self.assertEqual(run.status, SimulationRun.Status.PENDING)
        self.assertEqual(run.holds, SimulationRun.Holds.INDEX)

    def test_a_queued_run_refreshes_itself(self):
        with mock.patch("subscriptions.admin.run_simulation.delay"):
            posted = self.post(self.two_documents())
        response = self.client.get(posted["Location"])
        self.assertContains(response, 'http-equiv="refresh"')
        self.assertContains(response, "Queued")

    def test_a_finished_run_stops_refreshing_and_is_listed(self):
        response = self.simulate(self.two_documents())
        self.assertNotContains(response, 'http-equiv="refresh"')
        self.assertContains(
            self.client.get(self.url),
            reverse("admin:subscriptions-simulate-run", args=[self.latest_run().pk]),
        )

    def test_a_new_document_is_listed_with_who_it_reaches(self):
        self.snapshot_of({"rfc9110": meta(status="std", subseries=["std97"])})
        response = self.simulate(self.two_documents())
        report = response.context["report"]
        self.assertEqual(report["outcome"], "compared")
        self.assertEqual([row["doc_display"] for row in report["rows"]], ["RFC 9111"])
        self.assertEqual(report["rows"][0]["matched"], {"new_rfc": 2})
        by_user = {row["user"]: row["delivery"] for row in report["readers"]}
        self.assertEqual(
            by_user,
            {
                "reader": "web feed and email",
                f"user #{self.quiet.pk}": "web feed only: digest email is off",
            },
        )
        self.assertContains(response, "RFC 9111")
        self.assertEqual(self.latest_run().status, SimulationRun.Status.SUCCEEDED)

    def test_a_same_day_index_is_compared_as_normal(self):
        self.snapshot_of(
            {"rfc9110": meta(status="std", subseries=["std97"])},
            day=datetime.date.fromisoformat(INDEX_DAY),
        )
        report = self.simulate(self.two_documents()).context["report"]
        self.assertEqual(report["outcome"], "compared")
        self.assertEqual([row["doc_display"] for row in report["rows"]], ["RFC 9111"])

    def test_an_index_older_than_the_snapshot_would_not_be_compared(self):
        self.snapshot_of({"rfc9110": meta()}, day=datetime.date(2026, 9, 1))
        with self.assertLogs("reef", level="ERROR") as logs:
            response = self.simulate(self.two_documents())
        report = response.context["report"]
        self.assertEqual(report["outcome"], "older")
        self.assertEqual(report["rows"], [])
        self.assertContains(response, "Refuse to compare")
        # Marked, so that a rehearsal is not mistaken for the real run refusing.
        self.assertTrue(logs.records[0].getMessage().startswith("simulate: "))

    def test_without_a_snapshot_nothing_would_be_notified(self):
        report = self.simulate(self.two_documents()).context["report"]
        self.assertEqual(report["outcome"], "seeded")
        self.assertEqual(report["snapshot_state"], "none")
        self.assertEqual(report["rows"], [])

    def test_an_unreadable_snapshot_is_reported(self):
        DocumentSnapshot.objects.create(pk=1, payload=b"not compressed")
        with self.assertLogs("reef", level="ERROR"):
            report = self.simulate(self.two_documents()).context["report"]
        self.assertEqual(report["snapshot_state"], "unreadable")
        self.assertEqual(report["outcome"], "seeded")

    def test_document_set_subscribers_are_matched_against_the_uploaded_subseries(self):
        docset = DocumentSet.objects.create(owner=self.reader, title="Mine")
        DocumentSetEntry.objects.create(document_set=docset, doc="std97")
        Subscription.objects.create(
            user=self.reader, kind=Subscription.Kind.SET, document_set=docset
        )
        self.snapshot_of({"rfc9110": meta(status="ps", subseries=["std97"])})
        payload = index_payload([entry()])
        report = self.simulate(payload).context["report"]
        self.assertEqual(report["rows"][0]["matched"], {"set": 1})

    def test_text_that_is_not_json_is_reported(self):
        response = self.simulate(b"{nope")
        self.assertContains(response, "Not valid JSON")
        self.assertEqual(self.latest_run().status, SimulationRun.Status.FAILED)

    def test_json_that_is_not_an_index_is_reported_with_where(self):
        response = self.simulate({"createdOn": INDEX_DAY, "index": [{"number": 1}]})
        self.assertContains(
            response, "The upload does not match the rfc-index schema at index/0"
        )
        self.assertEqual(self.latest_run().status, SimulationRun.Status.FAILED)

    def test_a_large_diff_is_matched_in_full(self):
        """More changes than the simulator once capped at, every one matched, so
        the readers table counts every event rather than the first few hundred."""
        self.snapshot_of({"rfc9110": meta()})
        entries = [entry(number=n) for n in range(1, 602)]
        report = self.simulate(index_payload(entries)).context["report"]
        self.assertEqual(report["changes_found"], len(entries))
        self.assertEqual(len(report["rows"]), len(entries))
        self.assertEqual(
            {row["user"]: row["events"] for row in report["readers"]},
            {"reader": len(entries), f"user #{self.quiet.pk}": len(entries)},
        )

    def test_each_phase_is_timed_and_shown(self):
        self.snapshot_of({"rfc9110": meta()})
        response = self.simulate(self.two_documents())
        timings = self.latest_run().timings
        self.assertEqual(
            [timing["phase"] for timing in timings],
            ["parse", "validate", "reduce", "snapshot", "compare", "match", "readers"],
        )
        self.assertEqual(timings[0]["queries"], 0)
        self.assertGreater(timings[3]["queries"], 0)
        self.assertContains(response, "Timings")

    def test_the_upload_is_discarded_once_the_run_finishes(self):
        self.simulate(self.two_documents())
        self.assertEqual(bytes(self.latest_run().upload), b"")

    def test_a_run_that_raises_keeps_its_error_and_how_far_it_got(self):
        self.snapshot_of({"rfc9110": meta()})
        with (
            mock.patch.object(
                simulate_module,
                "plan_rfc_notifications",
                side_effect=RuntimeError("boom"),
            ),
            self.assertLogs("reef", level="ERROR"),
        ):
            response = self.simulate(self.two_documents())
        run = self.latest_run()
        self.assertEqual(run.status, SimulationRun.Status.FAILED)
        self.assertIn("RuntimeError: boom", run.error)
        self.assertEqual(run.timings[-1]["phase"], "match")
        self.assertIn("Matching", run.progress_message)
        self.assertEqual(bytes(run.upload), b"")
        self.assertContains(response, "boom")


class SimulateEntriesTests(SimulatorTestCase):
    """An upload of a few entries, applied to Red's live index fetched for the run."""

    def setUp(self):
        super().setUp()
        self.snapshot_of({"rfc9110": meta(status="std", subseries=["std97"])})
        fetch = mock.patch(
            "reef.rfcmeta.fetch_payload",
            return_value=index_payload([entry()], created_on=INDEX_DAY),
        )
        self.fetch = fetch.start()
        self.addCleanup(fetch.stop)

    def simulate_entries(self, payload):
        return self.simulate(payload, SimulationRun.Holds.ENTRIES)

    def test_an_uploaded_entry_is_added_to_the_live_index(self):
        response = self.simulate_entries([entry(number=99999, title="Fake")])
        report = response.context["report"]
        self.assertEqual(report["added"], [99999])
        self.assertEqual(report["replaced"], [])
        self.assertEqual(report["index_count"], 2)
        self.assertEqual([row["doc_display"] for row in report["rows"]], ["RFC 99999"])
        self.assertContains(response, "added RFC 99999")
        self.fetch.assert_called_once_with()

    def test_an_uploaded_entry_replaces_the_live_one_with_its_number(self):
        report = self.simulate_entries([entry(subseries=[])]).context["report"]
        self.assertEqual(report["replaced"], [9110])
        self.assertEqual(report["added"], [])
        [row] = report["rows"]
        self.assertEqual(row["doc_display"], "RFC 9110")
        self.assertEqual(
            [name for name, _before, _after in row["fields"]], ["subseries"]
        )

    def test_an_uploaded_created_on_is_used_in_place_of_the_live_one(self):
        report = self.simulate_entries(
            {"createdOn": "2026-08-01", "index": [entry(number=99999)]}
        ).context["report"]
        self.assertEqual(report["outcome"], "older")

    def test_an_upload_of_the_wrong_shape_is_reported(self):
        response = self.simulate_entries({"entries": []})
        self.assertContains(response, "Expected a list of index entries")
        self.fetch.assert_not_called()

    def test_an_entry_the_schema_refuses_is_reported_at_its_place_in_the_upload(self):
        response = self.simulate_entries([entry(number=99999), {"number": 99998}])
        self.assertContains(
            response, "The upload does not match the rfc-index schema at index/1"
        )

    def test_red_being_unreachable_is_reported(self):
        self.fetch.return_value = None
        response = self.simulate_entries([entry(number=99999)])
        self.assertContains(response, "Could not fetch Red&#x27;s live index")
        self.assertEqual(self.latest_run().status, SimulationRun.Status.FAILED)

    def test_the_live_index_is_not_cached(self):
        self.simulate_entries([entry(number=99999)])
        self.assertIsNone(rfcmeta._memo["value"])
        self.assertIsNone(cache.get(rfcmeta.CACHE_KEY))
        self.assertEqual(rfcmeta._abstracts, {})


class PersonalDataTests(SimulatorTestCase):
    """Only staff are named in what a run keeps or logs."""

    def setUp(self):
        super().setUp()
        self.snapshot_of({"rfc9110": meta(status="std", subseries=["std97"])})

    def test_a_reader_outside_staff_is_kept_by_id_only(self):
        self.simulate(self.two_documents())
        stored = json.dumps(self.latest_run().result)
        self.assertNotIn("quiet@example.org", stored)
        self.assertNotIn('"quiet"', stored)
        self.assertIn(f"user #{self.quiet.pk}", stored)

    def test_a_staff_reader_is_named(self):
        self.simulate(self.two_documents())
        [staff] = [
            row
            for row in self.latest_run().result["readers"]
            if row["user"] == "reader"
        ]
        self.assertEqual(staff["email"], "reader@staff.ietf.org")

    def test_the_arrival_log_names_only_staff(self):
        with (
            mock.patch("subscriptions.admin.run_simulation.delay"),
            self.assertLogs("reef", level="INFO") as logs,
        ):
            self.post(self.two_documents())
        [line] = [line for line in logs.output if "queued by" in line]
        self.assertIn(f"queued by user #{self.staff.pk}", line)
        self.assertNotIn("admin:", line)

        self.staff.email = "admin@staff.ietf.org"
        self.staff.save()
        with (
            mock.patch("subscriptions.admin.run_simulation.delay"),
            self.assertLogs("reef", level="INFO") as logs,
        ):
            self.post(self.two_documents())
        [line] = [line for line in logs.output if "queued by" in line]
        self.assertIn("queued by admin:", line)


class ProgressTests(SimulatorTestCase):
    def test_each_phase_is_announced_before_it_starts(self):
        self.snapshot_of({"rfc9110": meta(status="std", subseries=["std97"])})
        messages = []
        simulate(json.dumps(self.two_documents()).encode(), Phases(messages.append))
        self.assertEqual(
            messages,
            [
                "Reading the uploaded JSON",
                "Checking the index against the rfc-index schema",
                "Reducing the index to the watched fields",
                "Reading the live snapshot",
                "Comparing 2 documents with the snapshot",
                "Matching 1 change(s) against subscriptions",
                "Looking up 2 reader(s)",
            ],
        )

    def test_progress_can_be_written_while_the_simulation_runs(self):
        """Announced outside the read-only blocks: a write from inside one would
        be refused and fail the run."""
        run = SimulationRun.objects.create()

        def record(message):
            SimulationRun.objects.filter(pk=run.pk).update(progress_message=message)

        report = simulate(json.dumps(self.two_documents()).encode(), Phases(record))
        self.assertEqual(report.problem, "")
        run.refresh_from_db()
        self.assertEqual(run.progress_message, "Looking up 0 reader(s)")


class SimulateChangesNothingTests(SimulatorTestCase):
    """Every outcome, counted before and after, because this page can be opened
    against production."""

    TABLES = (
        DocumentSnapshot,
        Subscription,
        WebNotification,
        PendingNotification,
        SubjectNotificationEvent,
        User,
    )

    def counts(self):
        return {model.__name__: model.objects.count() for model in self.TABLES}

    def stored_snapshot(self):
        row = DocumentSnapshot.objects.filter(pk=1).first()
        return row and (bytes(row.payload), row.created_on, row.updated_at)

    def assert_nothing_changed(self, payload):
        counts, snapshot = self.counts(), self.stored_snapshot()
        with (
            CaptureQueriesContext(connection) as queries,
            mock.patch("subscriptions.tasks.deliver_notification.delay") as delay,
        ):
            response = self.simulate(payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.counts(), counts)
        self.assertEqual(self.stored_snapshot(), snapshot)
        delay.assert_not_called()
        writes = [
            q["sql"]
            for q in queries
            # The run's own row is the one thing a simulation writes.
            if '"subscriptions_simulationrun"' not in q["sql"]
            and not q["sql"]
            .lstrip()
            .upper()
            # The simulator's own request that PostgreSQL refuse writes.
            .startswith(
                (
                    "SELECT",
                    "SAVEPOINT",
                    "RELEASE",
                    "ROLLBACK",
                    "SET LOCAL TRANSACTION_READ_ONLY = ON",
                )
            )
        ]
        self.assertEqual(writes, [])
        self.assertIsNone(rfcmeta._memo["value"])
        self.assertIsNone(cache.get(rfcmeta.CACHE_KEY))
        self.assertEqual(rfcmeta._abstracts, {})

    def test_a_comparison_changes_nothing(self):
        self.snapshot_of({"rfc9110": meta()})
        self.assert_nothing_changed(self.two_documents())

    def test_a_refused_comparison_changes_nothing(self):
        self.snapshot_of({"rfc9110": meta()}, day=datetime.date(2026, 9, 1))
        with self.assertLogs("reef", level="ERROR"):
            self.assert_nothing_changed(self.two_documents())

    def test_a_seeding_run_changes_nothing(self):
        self.assert_nothing_changed(self.two_documents())

    def test_an_unreadable_snapshot_is_left_alone(self):
        DocumentSnapshot.objects.create(pk=1, payload=b"not compressed")
        with self.assertLogs("reef", level="ERROR"):
            self.assert_nothing_changed(self.two_documents())

    def test_a_rejected_upload_changes_nothing(self):
        self.snapshot_of({"rfc9110": meta()})
        self.assert_nothing_changed({"createdOn": INDEX_DAY, "index": [{"number": 1}]})
