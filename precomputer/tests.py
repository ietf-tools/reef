# Copyright The IETF Trust 2026, All Rights Reserved
import json
import re
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from popularity.models import PopularEntry
from precomputer.blobstore import LocalBlobStore, get_blob_store
from precomputer.tasks import TASKS
from ratings.models import Rating
from subjects.models import Subject, SubjectAssignment
from surveys.models import Survey

User = get_user_model()


class PrecomputeTestCase(TestCase):
    """Runs the command against a temporary output directory."""

    def setUp(self):
        self.out_dir = Path(tempfile.mkdtemp())
        overrides = override_settings(
            REEF_PRECOMPUTE_DIR=self.out_dir, REEF_PRECOMPUTE_S3_BUCKET=""
        )
        overrides.enable()
        self.addCleanup(overrides.disable)

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

    def test_payload_is_byte_identical_to_the_live_endpoint(self):
        PopularEntry.objects.create(rfc="rfc9110", rank=1)
        self.precompute("popularity")
        live = self.client.get("/api/reef/popularity/", HTTP_ACCEPT="application/json")
        self.assertEqual((self.out_dir / "popularity.json").read_bytes(), live.content)

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
