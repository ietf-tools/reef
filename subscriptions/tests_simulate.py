# Copyright The IETF Trust 2026, All Rights Reserved
"""The admin change-detection simulator, which must never change anything."""

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
    SubjectNotificationEvent,
    Subscription,
    WebNotification,
)
from subscriptions.simulate import read_only_database

User = get_user_model()

SNAPSHOT_DAY = datetime.date(2026, 8, 30)
INDEX_DAY = "2026-08-31"


def upload(payload):
    return SimpleUploadedFile(
        "rfc-index.json", json.dumps(payload).encode(), "application/json"
    )


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
            email="reader@example.org",
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

    def simulate(self, payload):
        return self.client.post(self.url, {"index": upload(payload)})

    def two_documents(self, day=INDEX_DAY):
        return index_payload(
            [entry(), entry(number=9111, title="HTTP Caching")], created_on=day
        )


class SimulateViewTests(SimulatorTestCase):
    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_staff_without_permission_is_refused(self):
        plain = User.objects.create(username="p", oidc_sub="s-p", is_staff=True)
        login(self.client, plain)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.simulate(self.two_documents()).status_code, 403)

    def test_get_shows_the_form(self):
        self.assertContains(self.client.get(self.url), "rfc-index.json")

    def test_a_new_document_is_listed_with_who_it_reaches(self):
        self.snapshot_of({"rfc9110": meta(status="std", subseries=["std97"])})
        response = self.simulate(self.two_documents())
        report = response.context["report"]
        self.assertEqual(report.outcome, "compared")
        self.assertEqual([row.doc_display for row in report.rows], ["RFC 9111"])
        self.assertEqual(report.rows[0].matched, {"new_rfc": 2})
        by_user = {row.user: row.delivery for row in report.readers}
        self.assertEqual(
            by_user,
            {
                "reader": "web feed and email",
                "quiet": "web feed only: digest email is off",
            },
        )
        self.assertContains(response, "RFC 9111")

    def test_a_same_day_index_is_compared_as_normal(self):
        self.snapshot_of(
            {"rfc9110": meta(status="std", subseries=["std97"])},
            day=datetime.date.fromisoformat(INDEX_DAY),
        )
        report = self.simulate(self.two_documents()).context["report"]
        self.assertEqual(report.outcome, "compared")
        self.assertEqual([row.doc_display for row in report.rows], ["RFC 9111"])

    def test_an_index_older_than_the_snapshot_would_not_be_compared(self):
        self.snapshot_of({"rfc9110": meta()}, day=datetime.date(2026, 9, 1))
        with self.assertLogs("reef", level="ERROR"):
            response = self.simulate(self.two_documents())
        report = response.context["report"]
        self.assertEqual(report.outcome, "older")
        self.assertEqual(report.rows, [])
        self.assertContains(response, "Refuse to compare")

    def test_without_a_snapshot_nothing_would_be_notified(self):
        report = self.simulate(self.two_documents()).context["report"]
        self.assertEqual(report.outcome, "seeded")
        self.assertEqual(report.snapshot_state, "none")
        self.assertEqual(report.rows, [])

    def test_an_unreadable_snapshot_is_reported(self):
        DocumentSnapshot.objects.create(pk=1, payload=b"not compressed")
        with self.assertLogs("reef", level="ERROR"):
            report = self.simulate(self.two_documents()).context["report"]
        self.assertEqual(report.snapshot_state, "unreadable")
        self.assertEqual(report.outcome, "seeded")

    def test_document_set_subscribers_are_matched_against_the_uploaded_subseries(self):
        docset = DocumentSet.objects.create(owner=self.reader, title="Mine")
        DocumentSetEntry.objects.create(document_set=docset, doc="std97")
        Subscription.objects.create(
            user=self.reader, kind=Subscription.Kind.SET, document_set=docset
        )
        self.snapshot_of({"rfc9110": meta(status="ps", subseries=["std97"])})
        payload = index_payload([entry()])
        report = self.simulate(payload).context["report"]
        self.assertEqual(report.rows[0].matched, {"set": 1})

    def test_text_that_is_not_json_is_reported(self):
        upload_file = SimpleUploadedFile("rfc-index.json", b"{nope")
        response = self.client.post(self.url, {"index": upload_file})
        self.assertContains(response, "Not valid JSON")
        self.assertIsNone(response.context["report"])

    def test_json_that_is_not_an_index_is_reported_with_where(self):
        response = self.simulate({"createdOn": INDEX_DAY, "index": [{"number": 1}]})
        self.assertContains(response, "Does not match the rfc-index schema at index/0")
        self.assertIsNone(response.context["report"])

    def test_a_large_diff_is_capped(self):
        self.snapshot_of({"rfc9110": meta()})
        entries = [entry(number=n) for n in range(1, 8)]
        with mock.patch.object(simulate_module, "MAX_CHANGES", 3):
            report = self.simulate(index_payload(entries)).context["report"]
        self.assertTrue(report.truncated)
        self.assertEqual(len(report.rows), 3)
        self.assertEqual(report.changes_found, len(entries))


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
            if not q["sql"]
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
