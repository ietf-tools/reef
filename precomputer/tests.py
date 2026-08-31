# Copyright The IETF Trust 2026, All Rights Reserved
import contextlib
import datetime
import json
import re
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command
from django.db import connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from popularity.models import PopularEntry
from precomputer.blobstore import LocalBlobStore, get_blob_store
from precomputer.registry import TASKS
from precomputer.signals import CURATED_DEBOUNCE_SECONDS
from precomputer.tasks import precompute_all, precompute_curated, precompute_engagement
from ratings.models import Rating
from reef import rfcmeta
from reef.locks import _key, advisory_lock
from subjects.models import Subject, SubjectAssignment
from surveys.models import Survey

User = get_user_model()


# Two documents' worth of Red's index, in the shape get_index() returns. Enough to
# cover resolved, unresolved and subseries without going near the network.
FAKE_INDEX_ENTRIES = [
    {
        "number": 9110,
        "title": "HTTP Semantics",
        "subseries": [{"type": "std", "number": 97}],
    },
    {
        "number": 2119,
        "title": "Key words",
        "subseries": [{"type": "bcp", "number": 14}],
    },
]


def fake_index(entries=None, created_on=None):
    return rfcmeta.DocumentIndex(
        rfcmeta._reduce(FAKE_INDEX_ENTRIES if entries is None else entries),
        created_on or datetime.date.today(),
    )


class PrecomputeTestCase(TestCase):
    """Runs the command against a temporary output directory.

    Red's index is stubbed for every test in here. Letting it through would put a
    6.8 MB fetch and a schema validation in front of each one, and make the suite
    fail when somebody runs it on a train.
    """

    def setUp(self):
        self.out_dir = Path(tempfile.mkdtemp())
        overrides = override_settings(
            REEF_PRECOMPUTE_DIR=self.out_dir, REEF_PRECOMPUTE_S3_BUCKET=""
        )
        overrides.enable()
        self.addCleanup(overrides.disable)

        self.index = fake_index()
        patcher = mock.patch("reef.rfcmeta.get_index", side_effect=lambda: self.index)
        self.get_index = patcher.start()
        self.addCleanup(patcher.stop)

    def precompute(self, *args, **options):
        out, err = StringIO(), StringIO()
        call_command("precompute", *args, stdout=out, stderr=err, **options)
        return out.getvalue()

    def written(self):
        return {
            str(path.relative_to(self.out_dir).as_posix())
            for path in self.out_dir.rglob("*")
            if path.is_file()
        }

    def read(self, key):
        return json.loads((self.out_dir / key).read_text())


class OutputTests(PrecomputeTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="a", oidc_sub="a")

    def test_empty_database_still_writes_the_whole_series_files(self):
        self.precompute()
        self.assertEqual(
            self.written(),
            {"stats.json", "popularity.json", "subjects.json", "surveys/open.json"},
        )
        self.assertEqual(self.read("stats.json"), [])

    def test_a_payload_that_adds_nothing_is_byte_identical_to_the_live_endpoint(self):
        Survey.objects.create(
            title="Open",
            slug="open-one",
            status=Survey.Status.PUBLISHED,
            visibility=Survey.Visibility.OPEN,
        )
        self.precompute("surveys")
        live = self.client.get(
            "/api/reef/surveys/open/", HTTP_ACCEPT="application/json"
        )
        self.assertEqual(
            (self.out_dir / "surveys/open.json").read_bytes(), live.content
        )

    def test_an_augmented_payload_is_the_live_response_plus_added_keys(self):
        """The invariant that replaced byte-identity: strip what the precomputer
        added and the rest must match the endpoint exactly, so nothing it writes can
        quietly disagree with what Reef serves."""
        PopularEntry.objects.create(rfc="rfc9110", rank=1)
        self.precompute("popularity")

        written = self.read("popularity.json")
        for row in written:
            del row["title"]
            del row["subseries"]

        live = self.client.get("/api/reef/popularity/", HTTP_ACCEPT="application/json")
        self.assertEqual(
            json.dumps(written, ensure_ascii=False, separators=(",", ":")).encode(),
            live.content,
        )

    def test_stats_covers_engagement(self):
        Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        self.precompute("stats")
        rows = {row["doc"]: row for row in self.read("stats.json")}
        self.assertEqual(rows["rfc9110"]["rating_average"], 4.0)
        self.assertEqual(rows["rfc9110"]["rating_count"], 1)

    def test_ratings_get_a_file_per_rated_document(self):
        Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        Rating.objects.create(rfc="bcp14", user=self.user, value=2)
        self.precompute("ratings")
        self.assertEqual(self.written(), {"ratings/rfc9110.json", "ratings/bcp14.json"})
        self.assertEqual(self.read("ratings/rfc9110.json")["average"], 4.0)

    def test_rating_file_is_the_anonymous_body(self):
        """your_rating is a per-caller field, so the stored copy must be null."""
        Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        self.precompute("ratings")
        self.assertIsNone(self.read("ratings/rfc9110.json")["your_rating"])

    def test_subjects_get_a_file_each(self):
        subject = Subject.objects.create(name="Security", slug="security")
        SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        self.precompute("subjects")
        self.assertEqual(self.written(), {"subjects.json", "subjects/security.json"})
        self.assertEqual(len(self.read("subjects.json")), 1)

    def test_only_open_surveys_get_a_definition(self):
        Survey.objects.create(
            title="Open",
            slug="open-one",
            status=Survey.Status.PUBLISHED,
            visibility=Survey.Visibility.OPEN,
        )
        Survey.objects.create(
            title="Signed in only",
            slug="private-one",
            status=Survey.Status.PUBLISHED,
            visibility=Survey.Visibility.AUTHENTICATED,
        )
        Survey.objects.create(title="Draft", slug="draft-one")
        self.precompute("surveys")
        self.assertEqual(
            self.written(),
            {"surveys/open.json", "surveys/open-one/definition.json"},
        )


class DocumentMetadataTests(PrecomputeTestCase):
    """Every file that names a document carries that document's metadata.

    The reason is Red's rather than Reef's: an SPA route wants one resource, and a
    page that fetches a list of identifiers and then resolves them loads slower than
    one that fetches a file it can render.
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="a", oidc_sub="a")

    def test_stats_rows_carry_metadata(self):
        Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        self.precompute("stats")
        row = next(r for r in self.read("stats.json") if r["doc"] == "rfc9110")
        self.assertEqual(row["title"], "HTTP Semantics")
        self.assertEqual(row["subseries"], ["std97"])
        self.assertEqual(row["rating_count"], 1)  # the endpoint's own fields survive

    def test_popularity_rows_carry_metadata(self):
        PopularEntry.objects.create(rfc="rfc2119", rank=1)
        self.precompute("popularity")
        row = self.read("popularity.json")[0]
        self.assertEqual(row["title"], "Key words")
        self.assertEqual(row["subseries"], ["bcp14"])

    def test_rating_files_carry_metadata(self):
        Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        self.precompute("ratings")
        self.assertEqual(self.read("ratings/rfc9110.json")["title"], "HTTP Semantics")

    def test_subject_detail_gains_a_map_and_keeps_its_documents_array(self):
        """A sibling map rather than retyping `documents`: retyping an existing key
        is the change that breaks a caller."""
        subject = Subject.objects.create(name="Security", slug="security")
        SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        self.precompute("subjects")
        payload = self.read("subjects/security.json")
        self.assertEqual(payload["documents"], ["rfc9110"])
        self.assertEqual(payload["document_meta"]["rfc9110"]["title"], "HTTP Semantics")

    def test_an_unresolvable_document_gets_null_metadata_not_omission(self):
        """Null rather than omitted or echoed back, so a reader can tell "no such
        document" from "not looked up"."""
        Rating.objects.create(rfc="rfc8446", user=self.user, value=4)
        self.precompute("stats")
        row = next(r for r in self.read("stats.json") if r["doc"] == "rfc8446")
        self.assertIsNone(row["title"])
        self.assertEqual(row["subseries"], [])

    def test_red_being_unreachable_still_writes_every_file(self):
        """Reef's own numbers do not depend on Red, so a failed fetch costs titles
        and nothing else."""
        self.index = None
        Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        self.precompute()
        row = next(r for r in self.read("stats.json") if r["doc"] == "rfc9110")
        self.assertIsNone(row["title"])
        self.assertIn("ratings/rfc9110.json", self.written())

    def test_no_metadata_skips_the_fetch_entirely(self):
        Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        self.precompute("stats", "--no-metadata")
        self.get_index.assert_not_called()
        row = next(r for r in self.read("stats.json") if r["doc"] == "rfc9110")
        self.assertIsNone(row["title"])

    def test_the_index_is_loaded_once_per_run_not_per_document(self):
        """Validating ten thousand entries is a couple of seconds; doing it per
        lookup would make a run unusable."""
        for number in (9110, 2119, 8446):
            Rating.objects.create(rfc=f"rfc{number}", user=self.user, value=3)
        self.precompute()
        self.assertEqual(self.get_index.call_count, 1)


class StaleIndexTests(PrecomputeTestCase):
    def setUp(self):
        super().setUp()
        user = User.objects.create(username="a", oidc_sub="a")
        Rating.objects.create(rfc="rfc9110", user=user, value=4)

    def _run_and_capture_logs(self, *args):
        with self.assertLogs("reef", level="INFO") as logs:
            self.precompute(*args)
        return "\n".join(logs.output)

    def test_a_fresh_index_logs_its_age_and_does_not_warn(self):
        output = self._run_and_capture_logs()
        self.assertIn("Red index: 2 documents", output)
        self.assertNotIn("over the", output)

    def test_an_old_index_warns_but_the_run_still_succeeds(self):
        stale = datetime.date.today() - datetime.timedelta(days=45)
        self.index = fake_index(created_on=stale)
        output = self._run_and_capture_logs()
        self.assertIn("45 days old, over the 30 day limit", output)
        self.assertIn("stats.json", self.written())

    def test_the_threshold_is_configurable(self):
        self.index = fake_index(
            created_on=datetime.date.today() - datetime.timedelta(days=5)
        )
        with override_settings(REEF_RFC_INDEX_MAX_AGE_DAYS=3):
            output = self._run_and_capture_logs()
        self.assertIn("over the 3 day limit", output)

    def test_a_document_reef_holds_that_red_lacks_is_warned_about(self):
        """The signal that actually matters: a frozen index does no harm until Reef
        knows about a document Red's copy does not."""
        user = User.objects.get(username="a")
        Rating.objects.create(rfc="rfc8446", user=user, value=2)
        output = self._run_and_capture_logs()
        self.assertIn("not in Red's index", output)
        self.assertIn("rfc8446", output)

    def test_nothing_is_warned_about_when_everything_resolves(self):
        output = self._run_and_capture_logs()
        self.assertNotIn("not in Red's index", output)


class SelectionTests(PrecomputeTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="a", oidc_sub="a")
        Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        Rating.objects.create(rfc="rfc8446", user=self.user, value=2)

    def test_named_tasks_run_alone(self):
        self.precompute("popularity")
        self.assertEqual(self.written(), {"popularity.json"})

    def test_unknown_task_is_refused(self):
        with self.assertRaises(CommandError):
            self.precompute("nonsense")

    def test_doc_narrows_per_document_tasks_only(self):
        self.precompute("--doc", "RFC 9110")
        written = self.written()
        self.assertIn("ratings/rfc9110.json", written)
        self.assertNotIn("ratings/rfc8446.json", written)
        # The whole-series files cover the named document too, so they are
        # still rebuilt in full.
        self.assertIn("stats.json", written)

    def test_doc_must_be_an_identifier(self):
        with self.assertRaises(CommandError):
            self.precompute("--doc", "not-a-document")

    def test_dry_run_writes_nothing(self):
        output = self.precompute("--dry-run")
        self.assertEqual(self.written(), set())
        self.assertIn("would write stats.json", output)


class PurgeTests(PrecomputeTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="a", oidc_sub="a")
        Rating.objects.create(rfc="rfc9110", user=self.user, value=4)

    def test_a_key_the_run_no_longer_produces_is_purged(self):
        self.precompute()
        Rating.objects.filter(rfc="rfc9110").delete()
        self.precompute()
        self.assertNotIn("ratings/rfc9110.json", self.written())

    def test_a_renamed_subject_leaves_no_stale_file(self):
        subject = Subject.objects.create(name="Security", slug="security")
        self.precompute("subjects")
        subject.slug = "sec"
        subject.save()
        self.precompute("subjects")
        self.assertEqual(self.written(), {"subjects.json", "subjects/sec.json"})

    def test_keys_no_task_owns_are_left_alone(self):
        stranger = self.out_dir / "other" / "nuxt-assets.json"
        stranger.parent.mkdir(parents=True)
        stranger.write_text("{}")
        self.precompute()
        self.assertIn("other/nuxt-assets.json", self.written())

    def test_a_task_that_did_not_run_is_not_purged(self):
        self.precompute()
        self.precompute("popularity")
        self.assertIn("ratings/rfc9110.json", self.written())

    def test_no_purge_keeps_stale_keys(self):
        self.precompute()
        Rating.objects.filter(rfc="rfc9110").delete()
        self.precompute("--no-purge")
        self.assertIn("ratings/rfc9110.json", self.written())

    def test_doc_skips_the_purge(self):
        """--doc rebuilds only one document's files, so absence proves nothing."""
        self.precompute()
        self.precompute("--doc", "rfc8446")
        self.assertIn("ratings/rfc9110.json", self.written())


class FailureTests(PrecomputeTestCase):
    """A task that raises must not take the run's other tasks down with it."""

    def setUp(self):
        super().setUp()
        user = User.objects.create(username="a", oidc_sub="a")
        Rating.objects.create(rfc="rfc9110", user=user, value=4)

    @staticmethod
    def broken(docs=None):
        raise RuntimeError("the database went away")
        yield  # pragma: no cover - marks broken as a generator, like a real task

    def with_broken_stats(self):
        broken = self.broken
        broken.owns = re.compile(r"^stats\.json$")
        broken.per_document = False
        return mock.patch.dict(TASKS, {"stats": broken})

    def test_the_other_tasks_still_run_and_the_command_fails(self):
        self.precompute()  # a good run first, so there is something to lose
        # Removed so that finding them again proves they were rebuilt rather
        # than left over from the run above.
        (self.out_dir / "popularity.json").unlink()
        (self.out_dir / "ratings" / "rfc9110.json").unlink()

        with self.with_broken_stats():
            with self.assertRaises(CommandError):
                self.precompute()

        self.assertIn("popularity.json", self.written())
        self.assertIn("ratings/rfc9110.json", self.written())

    def test_the_failed_task_leaves_its_previous_payload_in_place(self):
        self.precompute()
        with self.with_broken_stats():
            with self.assertRaises(CommandError):
                self.precompute()
        self.assertIn("stats.json", self.written())

    def test_a_failed_run_does_not_purge(self):
        """A missing key may be one the failed task simply did not rebuild."""
        self.precompute()
        Rating.objects.filter(rfc="rfc9110").delete()
        with self.with_broken_stats():
            with self.assertRaises(CommandError):
                self.precompute()
        self.assertIn("ratings/rfc9110.json", self.written())


class BlobStoreTests(TestCase):
    def test_local_store_is_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            with override_settings(
                REEF_PRECOMPUTE_S3_BUCKET="", REEF_PRECOMPUTE_DIR=tmp
            ):
                self.assertIsInstance(get_blob_store(), LocalBlobStore)

    def test_a_deployment_requiring_s3_refuses_to_use_a_directory(self):
        """Production sets this: a worker writing into its own container would log a
        successful run every hour and publish nothing."""
        with override_settings(
            REEF_PRECOMPUTE_S3_BUCKET="", REEF_PRECOMPUTE_REQUIRE_S3=True
        ):
            with self.assertRaises(ImproperlyConfigured):
                get_blob_store()

    def test_a_bucket_without_credentials_is_an_error_not_a_fallback(self):
        with override_settings(
            REEF_PRECOMPUTE_S3_BUCKET="reef",
            REEF_PRECOMPUTE_S3_ACCESS_KEY_ID="",
            REEF_PRECOMPUTE_S3_SECRET_ACCESS_KEY="",
        ):
            with self.assertRaises(ImproperlyConfigured):
                get_blob_store()

    def test_a_key_cannot_escape_the_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(tmp)
            with self.assertRaises(ValueError):
                store.put("../escaped.json", b"{}")

    def test_put_is_atomic_and_leaves_no_temporary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(tmp)
            store.put("a/b.json", b'{"x": 1}')
            self.assertEqual(store.list_keys(), ["a/b.json"])
            self.assertEqual((Path(tmp) / "a" / "b.json").read_bytes(), b'{"x": 1}')


class RegistryTests(PrecomputeTestCase):
    def test_every_task_owns_the_keys_it_produces(self):
        """A regex that missed its own keys would purge them on the next run."""
        user = User.objects.create(username="a", oidc_sub="a")
        Rating.objects.create(rfc="rfc9110", user=user, value=4)
        PopularEntry.objects.create(rfc="rfc9110", rank=1)
        Subject.objects.create(name="Security", slug="security")
        Survey.objects.create(
            title="Open",
            slug="open-one",
            status=Survey.Status.PUBLISHED,
            visibility=Survey.Visibility.OPEN,
        )

        for name, func in TASKS.items():
            with self.subTest(task=name):
                keys = [key for key, _body in func(docs=None)]
                self.assertTrue(keys, f"{name} produced nothing to check")
                for key in keys:
                    self.assertRegex(key, func.owns)


class AdvisoryLockTests(TransactionTestCase):
    """Uses TransactionTestCase: a session advisory lock taken on one connection is
    invisible to another only if both are real connections, which TestCase's outer
    transaction and single connection would hide."""

    def test_a_second_holder_is_refused_while_the_first_holds_it(self):
        with advisory_lock("precomputer.test") as first:
            self.assertTrue(first)
            other = connections.create_connection("default")
            try:
                with other.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_try_advisory_lock(%s)", [_key("precomputer.test")]
                    )
                    self.assertFalse(cursor.fetchone()[0])
            finally:
                other.close()

    def test_the_lock_is_released_on_exit(self):
        with advisory_lock("precomputer.test") as acquired:
            self.assertTrue(acquired)
        with advisory_lock("precomputer.test") as again:
            self.assertTrue(again)

    def test_the_lock_is_released_when_the_body_raises(self):
        with self.assertRaises(RuntimeError):
            with advisory_lock("precomputer.test"):
                raise RuntimeError("boom")
        with advisory_lock("precomputer.test") as again:
            self.assertTrue(again)

    def test_the_same_connection_can_take_a_lock_it_already_holds(self):
        """Postgres advisory locks are per session and re-entrant, so this guards
        against two workers rather than against one calling twice. That is the case
        it is for -- two runs are two processes -- but it is worth knowing that a
        single process is not stopped from re-entering."""
        with advisory_lock("precomputer.test") as first:
            with advisory_lock("precomputer.test") as again:
                self.assertTrue(first)
                self.assertTrue(again)

    def test_different_names_do_not_collide(self):
        with advisory_lock("precomputer.a") as a:
            with advisory_lock("precomputer.b") as b:
                self.assertTrue(a)
                self.assertTrue(b)


class CeleryTaskTests(PrecomputeTestCase):
    def setUp(self):
        super().setUp()
        PopularEntry.objects.create(rfc="rfc9110", rank=1)

    def test_precompute_all_runs_every_task(self):
        precompute_all()
        self.assertIn("stats.json", self.written())
        self.assertIn("popularity.json", self.written())

    def test_precompute_engagement_runs_only_its_own(self):
        precompute_engagement()
        self.assertEqual(self.written(), {"stats.json"})

    def test_precompute_curated_runs_only_its_own(self):
        precompute_curated()
        self.assertEqual(
            self.written(),
            {"popularity.json", "subjects.json", "surveys/open.json"},
        )

    def test_a_run_is_skipped_while_another_holds_the_lock(self):
        """The scheduled tick that lands on a run in progress does nothing, rather
        than queueing behind it to redo work that is being done."""
        with mock.patch("precomputer.tasks.advisory_lock") as lock:
            lock.return_value.__enter__.return_value = False
            self.assertFalse(precompute_all())
        self.assertEqual(self.written(), set())

    def test_a_failing_run_is_reported_rather_than_raised(self):
        """A raise here would earn a Celery retry that recomputes the same broken
        thing, and an alert for what the next tick fixes by itself."""
        with mock.patch(
            "precomputer.tasks.call_command", side_effect=CommandError("nope")
        ):
            self.assertFalse(precompute_all())


class CuratedSignalTests(TestCase):
    """Staff edits enqueue a refresh; reader activity does not."""

    def setUp(self):
        patcher = mock.patch("precomputer.tasks.precompute_curated.apply_async")
        self.enqueue = patcher.start()
        self.addCleanup(patcher.stop)

    def test_saving_a_curated_model_enqueues_a_run(self):
        with self.captureOnCommitCallbacks(execute=True):
            PopularEntry.objects.create(rfc="rfc9110", rank=1)
        self.assertEqual(self.enqueue.call_count, 1)
        self.assertEqual(
            self.enqueue.call_args.kwargs["countdown"], CURATED_DEBOUNCE_SECONDS
        )

    def test_deleting_a_curated_model_enqueues_a_run(self):
        subject = Subject.objects.create(name="Security", slug="security")
        self.enqueue.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            subject.delete()
        self.assertEqual(self.enqueue.call_count, 1)

    def test_reader_activity_does_not_enqueue_anything(self):
        """Ratings arrive continuously from Red; a task per write would enqueue
        thousands to rebuild a file nobody reads in between."""
        user = User.objects.create(username="a", oidc_sub="a")
        with self.captureOnCommitCallbacks(execute=True):
            Rating.objects.create(rfc="rfc9110", user=user, value=4)
        self.assertEqual(self.enqueue.call_count, 0)

    def test_a_rolled_back_edit_enqueues_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            with contextlib.suppress(RuntimeError), transaction.atomic():
                PopularEntry.objects.create(rfc="rfc7230", rank=3)
                raise RuntimeError("rolled back")
        self.assertEqual(self.enqueue.call_count, 0)

    def test_a_broker_failure_does_not_break_the_edit(self):
        self.enqueue.side_effect = OSError("broker down")
        with self.captureOnCommitCallbacks(execute=True):
            PopularEntry.objects.create(rfc="rfc8446", rank=2)  # must not raise
        self.assertEqual(PopularEntry.objects.filter(rfc="rfc8446").count(), 1)


class RetiredSubjectOutputTests(PrecomputeTestCase):
    """A retired subject is published only so a link naming it can be redirected."""

    def setUp(self):
        super().setUp()
        self.live = Subject.objects.create(name="Security and privacy", slug="secpriv")
        self.retired = Subject.objects.create(name="Security", slug="sec")
        self.retired.retire(merged_into=self.live)

    def test_it_is_absent_from_the_vocabulary(self):
        self.precompute("subjects")
        self.assertEqual([s["slug"] for s in self.read("subjects.json")], ["secpriv"])

    def test_its_file_is_the_redirect_and_nothing_else(self):
        self.precompute("subjects")
        self.assertEqual(
            self.read("subjects/sec.json"),
            {"slug": "sec", "retired": True, "merged_into": "secpriv"},
        )

    def test_a_live_subject_with_no_documents_still_carries_an_empty_map(self):
        """Keyed on the documents array being there, not on it having anything in
        it, so the live shape stays uniform."""
        self.precompute("subjects")
        payload = self.read("subjects/secpriv.json")
        self.assertEqual(payload["documents"], [])
        self.assertEqual(payload["document_meta"], {})
