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
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from docsets.models import DocumentSet, DocumentSetEntry
from popularity.models import DocumentPopularity
from precomputer.blobstore import LocalBlobStore, get_blob_store
from precomputer.models import PendingDocumentChange, PrecomputeRun
from precomputer.registry import TASKS
from precomputer.signals import CURATED_DEBOUNCE_SECONDS
from precomputer.tasks import (
    precompute_all,
    precompute_curated,
    precompute_engagement,
    precompute_from_admin,
    push_document_changes,
)
from ratings.models import Rating
from reef import rfcmeta
from reef.locks import _key, advisory_lock
from subjects.models import Subject, SubjectAlias, SubjectAssignment
from subjects.precompute import build_index
from subjects.tree import rollup
from subscriptions.models import Subscription
from surveys.models import Survey

User = get_user_model()


# Two documents' worth of Red's index, in the shape get_index() returns. Enough to
# cover resolved, unresolved and subseries without going near the network.
FAKE_INDEX_ENTRIES = [
    {
        "number": 9110,
        "title": "HTTP Semantics",
        "subseries": [{"type": "std", "number": 97}],
        "abstract": "What HTTP means by semantics.",
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


def rank(**scores):
    """Rows in the popularity table, the way a recompute writes them."""
    now = timezone.now()
    DocumentPopularity.objects.bulk_create(
        [
            DocumentPopularity(rfc=rfc, score=score, created_at=now, updated_at=now)
            for rfc, score in scores.items()
        ]
    )


class PrecomputeTestCase(TestCase):
    """Runs the command against a temporary output directory.

    Red's index is stubbed for every test in here. Letting it through would put a
    16.8 MB fetch and a schema validation in front of each one, and make the suite
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

        # The two ways the index reaches a task. Tasks that add metadata after
        # rendering take it as an argument, from get_index above. The subject
        # views resolve it themselves through cached_mapping, which in a
        # deployment is warm because the run loads the index before the first
        # task; nothing here runs that path, so the same fake is put behind both.
        cached = mock.patch(
            "reef.rfcmeta.cached_mapping",
            side_effect=lambda: None if self.index is None else self.index.mapping,
        )
        self.cached_mapping = cached.start()
        self.addCleanup(cached.stop)

        # subjects() calls load_index() itself, deliberately real-fetching every
        # run so _abstracts is warm regardless of the rest of the run's cache
        # state; here that must not reach the network, so it is faked the same
        # way as get_index above, and repopulates _abstracts from the same fake
        # entries every call rather than once in setUp.
        def _load_index():
            rfcmeta._abstracts.clear()
            rfcmeta._abstracts.update(rfcmeta._reduce_abstracts(FAKE_INDEX_ENTRIES))
            return self.index

        load_index_patcher = mock.patch(
            "reef.rfcmeta.load_index", side_effect=_load_index
        )
        self.load_index = load_index_patcher.start()
        self.addCleanup(load_index_patcher.stop)
        self.addCleanup(rfcmeta._abstracts.clear)

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
            {
                "stats.json",
                "popularity.json",
                "subjects.json",
                "surveys/open.json",
                "surveys/published.json",
            },
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

    def test_a_document_file_is_the_live_response_byte_for_byte(self):
        """Nothing the precomputer writes may disagree with what Reef serves: the
        contract describes the endpoint, and the file is a cache of it."""
        Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        self.precompute("stats", "ratings")

        for key, url in (
            ("stats.json", "/api/reef/stats/"),
            ("ratings/rfc9110.json", "/api/reef/ratings/rfc9110/"),
        ):
            live = self.client.get(url, HTTP_ACCEPT="application/json")
            self.assertEqual((self.out_dir / key).read_bytes(), live.content, key)

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
        self.assertEqual(len(self.read("subjects.json")["subjects"]), 1)

    def test_published_list_carries_authenticated_surveys_too(self):
        """The whole point of the second list: Red offers a signed-in reader a
        survey without first asking the API who they are."""
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

        published = {
            row["slug"]: row["visibility"]
            for row in self.read("surveys/published.json")
        }
        self.assertEqual(
            published, {"open-one": "open", "private-one": "authenticated"}
        )
        self.assertEqual(
            [row["slug"] for row in self.read("surveys/open.json")], ["open-one"]
        )

    def test_published_list_leaves_out_a_withdrawn_survey(self):
        survey = Survey.objects.create(
            title="Signed in only",
            slug="private-one",
            status=Survey.Status.PUBLISHED,
            visibility=Survey.Visibility.AUTHENTICATED,
        )
        survey.soft_delete()
        self.precompute("surveys")
        self.assertEqual(self.read("surveys/published.json"), [])

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
            {
                "surveys/open.json",
                "surveys/published.json",
                "surveys/open-one/definition.json",
            },
        )


class SubjectIndexTests(PrecomputeTestCase):
    """subjects.json: the whole vocabulary in one file.

    Red fetches it per route and renders from it, so it carries the tree, the
    assignments and the titles in one file. What it must not do is carry any of
    them twice.
    """

    def setUp(self):
        super().setUp()
        self.messaging = Subject.objects.create(name="Messaging", slug="messaging")
        self.email = Subject.objects.create(
            name="Email", slug="email", parent=self.messaging
        )
        SubjectAssignment.objects.create(subject=self.email, doc="rfc9110")
        SubjectAssignment.objects.create(subject=self.messaging, doc="rfc2119")

    def published(self):
        self.precompute("subjects")
        return self.read("subjects.json")

    def test_it_is_two_keyed_maps(self):
        payload = self.published()
        self.assertEqual(set(payload), {"documents", "subjects"})
        # Keyed by slug, which is globally unique, so a caller looks a subject up
        # by the name it holds rather than by reconstructing a path.
        self.assertEqual(list(payload["subjects"]), ["messaging", "email"])

    def test_subjects_are_keyed_by_slug_in_tree_order(self):
        payload = self.published()
        self.assertEqual(
            [entry["path"] for entry in payload["subjects"].values()],
            ["messaging", "messaging/email"],
        )

    def test_an_entry_carries_the_tree_and_both_counts(self):
        entry = self.published()["subjects"]["messaging"]
        self.assertIsNone(entry["parent"])
        self.assertEqual(entry["children"], ["email"])
        self.assertEqual(entry["documents"], ["rfc2119"])
        self.assertEqual(entry["document_count"], 1)
        # rfc9110 sits under email, so messaging covers both.
        self.assertEqual(entry["document_count_deep"], 2)

    def test_a_child_names_its_parent(self):
        entry = self.published()["subjects"]["email"]
        self.assertEqual(entry["parent"], "messaging")
        self.assertEqual(entry["children"], [])

    def test_metadata_is_carried_once_and_referenced_by_identifier(self):
        payload = self.published()
        self.assertEqual(
            payload["documents"]["rfc9110"],
            {"title": "HTTP Semantics", "subseries": ["std97"]},
        )
        # Not beside each subject that covers the document, which at this
        # vocabulary's depth would repeat every title about three times over.
        for entry in payload["subjects"].values():
            self.assertNotIn("document_meta", entry)
            self.assertEqual(entry["documents"], list(entry["documents"]))

    def test_the_subtree_is_not_written_out(self):
        # Derivable from path and children in the pass a caller already makes;
        # writing it would store every identifier once per ancestor.
        for entry in self.published()["subjects"].values():
            self.assertNotIn("documents_in_subtree", entry)

    def test_only_the_documents_the_file_mentions_are_described(self):
        Subject.objects.create(name="Web", slug="web")
        payload = self.published()
        self.assertEqual(sorted(payload["documents"]), ["rfc2119", "rfc9110"])

    def test_a_retired_subject_is_absent_but_its_documents_are_not_lost(self):
        # The vocabulary is the offer, so a retired subject leaves it. Its
        # assignments stay where they are, which is why the parent still covers
        # them.
        self.email.retire()
        payload = self.published()
        self.assertEqual(list(payload["subjects"]), ["messaging"])

    def test_the_whole_vocabulary_costs_a_bounded_number_of_queries(self):
        # The trap this shape had to avoid: a subtree query per subject. rollup()
        # is two and the rows are one, and none of the three grows with the size
        # of the vocabulary.
        for number in range(20):
            Subject.objects.create(name=f"S{number}", slug=f"s{number}")
        with self.assertNumQueries(3):
            build_index()

    def test_the_subjects_task_rolls_up_once_for_the_whole_run(self):
        """The trap subject_meta's counts had to avoid too: recomputing rollup()
        once per subject file would turn one whole-vocabulary pass into one per
        file. registry.subjects() computes it once and hands the same result to
        the index and to every per-subject render."""
        for number in range(20):
            Subject.objects.create(name=f"S{number}", slug=f"s{number}")
        with mock.patch("precomputer.registry.rollup", wraps=rollup) as rolled_up:
            self.precompute("subjects")
        self.assertEqual(rolled_up.call_count, 1)


class DocumentMetadataTests(PrecomputeTestCase):
    """Only the subject files carry document metadata, and they declare it on
    their own serializers, so the contract describes it. Every other file is the
    endpoint's bytes, title-less, since its reader already holds the documents.
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="a", oidc_sub="a")

    def test_document_files_carry_no_metadata(self):
        Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        rank(rfc2119=1.0)
        self.precompute("stats", "ratings", "popularity")
        stats_row = next(r for r in self.read("stats.json") if r["doc"] == "rfc9110")
        for row in (
            stats_row,
            self.read("ratings/rfc9110.json"),
            self.read("popularity.json")["entries"][0],
        ):
            self.assertNotIn("title", row)
            self.assertNotIn("subseries", row)
        self.assertEqual(stats_row["rating_count"], 1)

    def test_an_alias_gets_the_redirect_stub_as_its_own_file(self):
        """A name arrives from a link without the caller knowing which kind it is,
        so it has to be answered from the same directory as a subject's own slug."""
        subject = Subject.objects.create(name="Security", slug="security")
        SubjectAlias.objects.create(slug="sec", subject=subject)
        self.precompute("subjects")
        self.assertEqual(
            self.written(),
            {"subjects.json", "subjects/security.json", "subjects/sec.json"},
        )
        self.assertEqual(
            self.read("subjects/sec.json"), {"slug": "sec", "alias_of": "security"}
        )

    def test_a_shadowed_alias_does_not_overwrite_the_subjects_file(self):
        """bulk_create goes around the validation that refuses this. The read serves
        the subject for that name, so the file has to as well."""
        subject = Subject.objects.create(name="Security", slug="security")
        SubjectAlias.objects.bulk_create(
            [SubjectAlias(slug="routing", subject=subject)]
        )
        Subject.objects.create(name="Routing", slug="routing")
        self.precompute("subjects")
        self.assertIn("documents", self.read("subjects/routing.json"))

    def test_subject_detail_gains_a_map_and_keeps_its_documents_array(self):
        """A sibling map rather than retyping `documents`: retyping an existing key
        is the change that breaks a caller."""
        subject = Subject.objects.create(name="Security", slug="security")
        SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        self.precompute("subjects")
        payload = self.read("subjects/security.json")
        self.assertEqual(payload["documents"], ["rfc9110"])
        self.assertEqual(payload["document_meta"]["rfc9110"]["title"], "HTTP Semantics")

    def test_only_the_subject_file_carries_the_fuller_metadata(self):
        """The index lists every document a subject covers, and repeating the full
        set there once per subject would bloat a file most readers never need it
        from; only a document's own subject file is worth that cost."""
        subject = Subject.objects.create(name="Security", slug="security")
        SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        self.precompute("subjects")
        self.assertEqual(
            self.read("subjects.json")["documents"]["rfc9110"],
            {"title": "HTTP Semantics", "subseries": ["std97"]},
        )
        detail = self.read("subjects/security.json")["document_meta"]["rfc9110"]
        self.assertEqual(detail["abstract"], "What HTTP means by semantics.")
        self.assertIn("status", detail)

    def test_red_being_unreachable_still_writes_every_file(self):
        """Reef's own numbers do not depend on Red, so a failed fetch costs the
        stale-source check and nothing else."""
        self.index = None
        Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        self.precompute()
        self.assertIn("stats.json", self.written())
        self.assertIn("ratings/rfc9110.json", self.written())

    def test_no_metadata_skips_the_fetch_entirely(self):
        Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        self.precompute("stats", "--no-metadata")
        self.get_index.assert_not_called()
        self.assertIn("stats.json", self.written())

    def test_the_index_is_loaded_once_per_run_not_per_document(self):
        """Per-lookup validation of ten thousand entries would make a run unusable."""
        for number in (9110, 2119, 8446):
            Rating.objects.create(rfc=f"rfc{number}", user=self.user, value=3)
        self.precompute()
        self.assertEqual(self.get_index.call_count, 1)


class SubjectNameTests(PrecomputeTestCase):
    """A subject's file carries its branch of the vocabulary: ancestors and
    descendants, with names, so a page drawing a breadcrumb or a nested listing
    does not have to fetch the whole vocabulary to turn `email` into "Email".

    `subject_meta` is `{ancestors, descendants}`. `ancestors` is a flat,
    root-first list -- the chain up is a single line, nothing to nest.
    `descendants` is a real tree: each node carries its own `children`, the
    same shape recursively, so a page can render the whole subtree beneath
    this subject without flattening it back into slugs and path segments
    itself. Both list the same fields a listing reads off
    `Subject`/`SubjectIndexEntry` elsewhere: name, description, and both
    document counts.
    """

    def setUp(self):
        super().setUp()
        self.messaging = Subject.objects.create(
            name="Messaging", slug="messaging", description="Messaging protocols"
        )
        self.email = Subject.objects.create(
            name="Email", slug="email", parent=self.messaging, description="Email"
        )
        self.dkim = Subject.objects.create(name="DKIM", slug="dkim", parent=self.email)

    def published(self, slug):
        self.precompute("subjects")
        return self.read(f"subjects/{slug}.json")

    def test_it_names_the_ancestors_and_the_descendants(self):
        payload = self.published("email")
        self.assertEqual(
            payload["subject_meta"],
            {
                "ancestors": [
                    {
                        "slug": "messaging",
                        "name": "Messaging",
                        "description": "Messaging protocols",
                        "document_count": 0,
                        "document_count_deep": 0,
                    },
                ],
                "descendants": [
                    {
                        "slug": "dkim",
                        "name": "DKIM",
                        "description": "",
                        "document_count": 0,
                        "document_count_deep": 0,
                        "children": [],
                    },
                ],
            },
        )

    def test_it_does_not_name_the_subject_itself(self):
        # The subject carries its own `name` already, and repeating it would be a
        # second place for a rename to have to reach.
        payload = self.published("email")
        slugs = [entry["slug"] for entry in payload["subject_meta"]["ancestors"]]
        slugs += [entry["slug"] for entry in payload["subject_meta"]["descendants"]]
        self.assertNotIn("email", slugs)

    def test_ancestors_go_root_first(self):
        payload = self.published("dkim")
        self.assertEqual(
            [entry["slug"] for entry in payload["subject_meta"]["ancestors"]],
            ["messaging", "email"],
        )

    def test_descendants_reach_every_depth_nested_under_its_own_parent(self):
        """dkim is messaging's grandchild, not its child, so a flat or a
        one-level lookup would either miss it or misplace it -- nested under
        email is what a page needs to render the whole subtree correctly."""
        payload = self.published("messaging")
        descendants = payload["subject_meta"]["descendants"]
        self.assertEqual([entry["slug"] for entry in descendants], ["email"])
        self.assertEqual(
            [entry["slug"] for entry in descendants[0]["children"]], ["dkim"]
        )

    def test_it_does_not_reach_a_sibling(self):
        Subject.objects.create(
            name="Chat", slug="chat", parent=self.messaging, description="Chat"
        )
        payload = self.published("email")
        slugs = [entry["slug"] for entry in payload["subject_meta"]["ancestors"]]
        slugs += [entry["slug"] for entry in payload["subject_meta"]["descendants"]]
        self.assertNotIn("chat", slugs)

    def test_counts_are_the_roll_up_direct_and_covered_by_path(self):
        """Not queried per row: the same roll-up subjects.json's own counts come
        from, which is what keeps a wide subtree from costing a query per
        ancestor and per descendant."""
        SubjectAssignment.objects.create(subject=self.email, doc="rfc9110")
        SubjectAssignment.objects.create(subject=self.dkim, doc="rfc2119")
        payload = self.published("dkim")
        ancestors = {
            entry["slug"]: entry for entry in payload["subject_meta"]["ancestors"]
        }
        self.assertEqual(ancestors["messaging"]["document_count"], 0)
        # Covers both rfc9110 (on email, its child) and rfc2119 (on dkim itself).
        self.assertEqual(ancestors["messaging"]["document_count_deep"], 2)
        self.assertEqual(ancestors["email"]["document_count"], 1)
        self.assertEqual(ancestors["email"]["document_count_deep"], 2)

    def test_a_retired_child_is_not_offered(self):
        # subject_meta describes what the file points at, and `children` is live
        # subjects only, so a retired one must not appear in either.
        self.dkim.retire()
        payload = self.published("email")
        self.assertEqual(payload["children"], [])
        self.assertEqual(
            payload["subject_meta"],
            {
                "ancestors": [
                    {
                        "slug": "messaging",
                        "name": "Messaging",
                        "description": "Messaging protocols",
                        "document_count": 0,
                        "document_count_deep": 0,
                    }
                ],
                "descendants": [],
            },
        )

    def test_a_root_with_no_children_carries_empty_lists(self):
        Subject.objects.create(name="Routing", slug="routing")
        self.assertEqual(
            self.published("routing")["subject_meta"],
            {"ancestors": [], "descendants": []},
        )

    def test_a_redirect_stub_carries_neither_map(self):
        # A retired subject's payload is a redirect and nothing else, so there is
        # nothing for either map to describe.
        self.dkim.retire(merged_into=self.email)
        payload = self.published("dkim")
        self.assertNotIn("subject_meta", payload)
        self.assertNotIn("document_meta", payload)

    def test_naming_the_ancestors_costs_the_same_however_deep(self):
        """Ancestors are the prefixes of `path` fetched in one `path__in`, not a
        walk up the tree, so a subject four levels down costs what a root does."""
        from subjects.precompute import PrecomputedSubjectDetailSerializer

        def queries_for(subject):
            with CaptureQueriesContext(connections["default"]) as captured:
                _ = PrecomputedSubjectDetailSerializer(subject).data
            return len(captured)

        # Two ancestors against one, not a root against a leaf: a root skips the
        # lookup altogether, so comparing with one would pass on the guard rather
        # than on the query being singular.
        self.assertEqual(queries_for(self.dkim), queries_for(self.email))

    def test_a_caller_with_no_precomputed_tree_still_gets_the_whole_branch(self):
        """The fallback a caller outside a precompute run takes -- no
        subject_tree in context, so this queries instead -- has to answer the
        same question a warm tree does: every descendant nested under its own
        parent, not just direct children, and not flattened."""
        from subjects.precompute import PrecomputedSubjectDetailSerializer

        payload = PrecomputedSubjectDetailSerializer(self.messaging).data
        descendants = payload["subject_meta"]["descendants"]
        self.assertEqual([entry["slug"] for entry in descendants], ["email"])
        self.assertEqual(
            [entry["slug"] for entry in descendants[0]["children"]], ["dkim"]
        )


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

    def test_a_deleted_subject_leaves_no_stale_file(self):
        subject = Subject.objects.create(name="Security", slug="security")
        self.precompute("subjects")
        subject.delete()
        self.precompute("subjects")
        self.assertEqual(self.written(), {"subjects.json"})

    def test_a_renamed_subject_keeps_its_old_key_as_a_redirect(self):
        """A rename leaves an alias behind, so the old key is one the run still
        produces: the stub that sends a reader following an old link to the new
        name."""
        subject = Subject.objects.create(name="Security", slug="security")
        self.precompute("subjects")
        subject.slug = "sec"
        subject.save()
        self.precompute("subjects")
        self.assertEqual(
            self.written(),
            {"subjects.json", "subjects/sec.json", "subjects/security.json"},
        )
        self.assertEqual(
            self.read("subjects/security.json"),
            {"slug": "security", "alias_of": "sec"},
        )

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
        rank(rfc9110=1.0)
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
        rank(rfc9110=1.0)

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
            {
                "popularity.json",
                "subjects.json",
                "surveys/open.json",
                "surveys/published.json",
            },
        )

    def test_a_run_is_skipped_while_another_holds_the_lock(self):
        """The scheduled tick that lands on a run in progress does nothing, rather
        than queueing behind it to redo work that is being done."""
        with mock.patch("precomputer.tasks.advisory_lock") as lock:
            lock.return_value.__enter__.return_value = False
            self.assertFalse(precompute_all())
        self.assertEqual(self.written(), set())

    def test_a_failing_run_is_reported_rather_than_raised(self):
        """A raise would earn a Celery retry that recomputes the same broken thing."""
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
            Subject.objects.create(name="Security", slug="security")
        self.assertEqual(self.enqueue.call_count, 1)
        self.assertEqual(
            self.enqueue.call_args.kwargs["countdown"], CURATED_DEBOUNCE_SECONDS
        )

    def test_a_recomputed_ranking_enqueues_nothing(self):
        """Bulk-written, and published by the import that changed it."""
        with self.captureOnCommitCallbacks(execute=True):
            rank(rfc9110=1.0)
        self.assertEqual(self.enqueue.call_count, 0)

    def test_deleting_a_curated_model_enqueues_a_run(self):
        subject = Subject.objects.create(name="Security", slug="security")
        self.enqueue.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            subject.delete()
        self.assertEqual(self.enqueue.call_count, 1)

    def test_editing_a_survey_enqueues_a_run(self):
        """Any field, not just the ones a slug or a count is derived from: the
        published lists carry the title and description a staff edit changes."""
        with self.captureOnCommitCallbacks(execute=True):
            survey = Survey.objects.create(
                slug="sat", title="Before", status=Survey.Status.PUBLISHED
            )
        self.enqueue.reset_mock()

        survey.title = "After"
        with self.captureOnCommitCallbacks(execute=True):
            survey.save()
        self.assertEqual(self.enqueue.call_count, 1)

    def test_withdrawing_a_survey_enqueues_a_run(self):
        """soft_delete is a save, not a delete, so the post_delete receiver never
        sees it -- and a withdrawn survey has to stop being offered."""
        with self.captureOnCommitCallbacks(execute=True):
            survey = Survey.objects.create(
                slug="sat", title="Satisfaction", status=Survey.Status.PUBLISHED
            )
        self.enqueue.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            survey.soft_delete()
        self.assertEqual(self.enqueue.call_count, 1)

    def test_reader_activity_does_not_enqueue_anything(self):
        """A task per rating would enqueue thousands to rebuild a file nobody reads."""
        user = User.objects.create(username="a", oidc_sub="a")
        with self.captureOnCommitCallbacks(execute=True):
            Rating.objects.create(rfc="rfc9110", user=user, value=4)
        self.assertEqual(self.enqueue.call_count, 0)

    def test_a_rolled_back_edit_enqueues_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            with contextlib.suppress(RuntimeError), transaction.atomic():
                Subject.objects.create(name="Transport", slug="transport")
                raise RuntimeError("rolled back")
        self.assertEqual(self.enqueue.call_count, 0)

    def test_a_broker_failure_does_not_break_the_edit(self):
        self.enqueue.side_effect = OSError("broker down")
        with self.captureOnCommitCallbacks(execute=True):
            Subject.objects.create(name="Transport", slug="transport")  # must not raise
        self.assertEqual(Subject.objects.filter(slug="transport").count(), 1)


class RetiredSubjectOutputTests(PrecomputeTestCase):
    """A retired subject is published only so a link naming it can be redirected."""

    def setUp(self):
        super().setUp()
        self.live = Subject.objects.create(name="Security and privacy", slug="secpriv")
        self.retired = Subject.objects.create(name="Security", slug="sec")
        self.retired.retire(merged_into=self.live)

    def test_it_is_absent_from_the_vocabulary(self):
        self.precompute("subjects")
        self.assertEqual(list(self.read("subjects.json")["subjects"]), ["secpriv"])

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


class PrecomputeAdminViewTests(PrecomputeTestCase):
    """The staff-only /admin/precompute/ button. It only ever creates a
    PrecomputeRun and hands it to Celery; the run itself is
    PrecomputeFromAdminTaskTests' job."""

    def setUp(self):
        super().setUp()
        self.url = reverse("admin:precomputer-run")

    def _staff(self):
        return User.objects.create(username="admin", oidc_sub="s-admin", is_staff=True)

    def test_anonymous_is_sent_to_login(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login/", resp["Location"])

    def test_non_staff_is_sent_to_login(self):
        user = User.objects.create(username="plain", oidc_sub="s-plain", is_staff=False)
        self.client.force_login(user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)

    def test_staff_get_shows_the_form_and_recent_runs(self):
        staff = self._staff()
        PrecomputeRun.objects.create(triggered_by=staff)
        self.client.force_login(staff)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Run precompute now")
        self.assertContains(resp, "Recent runs")

    def test_staff_post_creates_a_run_and_enqueues_it_without_running_it(self):
        staff = self._staff()
        self.client.force_login(staff)
        with mock.patch("precomputer.admin.precompute_from_admin.delay") as delay:
            resp = self.client.post(self.url)
        run = PrecomputeRun.objects.get()
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"/precompute/runs/{run.pk}/", resp["Location"])
        self.assertEqual(run.status, PrecomputeRun.Status.PENDING)
        self.assertEqual(run.triggered_by, staff)
        delay.assert_called_once_with(run.pk)
        self.assertEqual(self.written(), set())

    def test_an_unfinished_run_refreshes_and_shows_its_progress(self):
        run = PrecomputeRun.objects.create(
            status=PrecomputeRun.Status.RUNNING,
            progress_message="[subjects] 150/660 uploaded",
        )
        self.client.force_login(self._staff())
        resp = self.client.get(reverse("admin:precomputer-run-detail", args=[run.pk]))
        self.assertContains(resp, 'http-equiv="refresh"')
        self.assertContains(resp, "[subjects] 150/660 uploaded")

    def test_a_finished_run_stops_refreshing_and_shows_its_output(self):
        run = PrecomputeRun.objects.create(
            status=PrecomputeRun.Status.FAILED, output="[stats] 1 file(s)", error="boom"
        )
        self.client.force_login(self._staff())
        resp = self.client.get(reverse("admin:precomputer-run-detail", args=[run.pk]))
        self.assertNotContains(resp, 'http-equiv="refresh"')
        self.assertContains(resp, "[stats] 1 file(s)")
        self.assertContains(resp, "boom")


class PrecomputeFromAdminTaskTests(PrecomputeTestCase):
    def test_a_missing_run_is_logged_not_raised(self):
        precompute_from_admin(999999)  # must not raise

    def test_a_run_writes_every_task_and_records_its_output(self):
        run = PrecomputeRun.objects.create()
        precompute_from_admin(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, PrecomputeRun.Status.SUCCEEDED)
        self.assertIn("stats.json", self.written())
        self.assertIn("file(s) written", run.output)
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.finished_at)

    def test_the_last_line_of_output_is_left_as_its_progress(self):
        run = PrecomputeRun.objects.create()
        precompute_from_admin(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.progress_message, run.output.strip().splitlines()[-1])

    def test_a_run_while_another_holds_the_lock_is_skipped(self):
        run = PrecomputeRun.objects.create()
        with mock.patch("precomputer.tasks.advisory_lock") as lock:
            lock.return_value.__enter__.return_value = False
            precompute_from_admin(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, PrecomputeRun.Status.SKIPPED)
        self.assertIn("already in progress", run.error)
        self.assertEqual(self.written(), set())

    def test_a_failing_command_marks_the_run_failed(self):
        run = PrecomputeRun.objects.create()
        with mock.patch(
            "precomputer.tasks.call_command", side_effect=CommandError("boom")
        ):
            precompute_from_admin(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, PrecomputeRun.Status.FAILED)
        self.assertIn("boom", run.error)

    def test_an_unexpected_exception_records_its_traceback(self):
        """Nobody pressing the button can read the worker's log, so the row
        carries the whole traceback rather than just the message."""
        run = PrecomputeRun.objects.create()
        with mock.patch(
            "precomputer.tasks.call_command", side_effect=RuntimeError("boom")
        ):
            precompute_from_admin(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, PrecomputeRun.Status.FAILED)
        self.assertIn("Traceback (most recent call last)", run.error)
        self.assertIn("RuntimeError: boom", run.error)


class ProgressOutputTests(PrecomputeTestCase):
    def test_uploads_are_counted_as_they_complete(self):
        for index in range(3):
            Subject.objects.create(name=f"Subject {index}", slug=f"subject-{index}")
        with mock.patch(
            "precomputer.management.commands.precompute.PROGRESS_INTERVAL", 2
        ):
            output = self.precompute("subjects")
        self.assertIn("[subjects] rendering", output)
        self.assertIn("[subjects] 2 file(s) rendered", output)
        self.assertIn("[subjects] 2/4 uploaded", output)
        self.assertIn("[subjects] 4/4 uploaded", output)

    def test_a_dry_run_counts_nothing_as_uploaded(self):
        output = self.precompute("stats", dry_run=True)
        self.assertNotIn("uploaded", output)


class DocumentChangeMarkTests(TestCase):
    """Reader activity marks the document; it does not enqueue a run."""

    def setUp(self):
        self.user = User.objects.create(username="reader", oidc_sub="reader")

    def pending(self):
        return list(PendingDocumentChange.objects.values_list("doc", flat=True))

    def test_a_rating_marks_its_document_once(self):
        with self.captureOnCommitCallbacks(execute=True):
            Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        first = PendingDocumentChange.objects.get(doc="rfc9110")
        other = User.objects.create(username="other", oidc_sub="other")
        with self.captureOnCommitCallbacks(execute=True):
            Rating.objects.create(rfc="rfc9110", user=other, value=2)
        self.assertEqual(self.pending(), ["rfc9110"])
        self.assertGreater(
            PendingDocumentChange.objects.get(doc="rfc9110").last_seen, first.last_seen
        )

    def test_deleting_a_rating_marks_its_document(self):
        with self.captureOnCommitCallbacks(execute=True):
            rating = Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
        PendingDocumentChange.objects.all().delete()
        with self.captureOnCommitCallbacks(execute=True):
            rating.delete()
        self.assertEqual(self.pending(), ["rfc9110"])

    def test_a_set_entry_marks_its_document(self):
        document_set = DocumentSet.objects.create(owner=self.user, title="Mine")
        with self.captureOnCommitCallbacks(execute=True):
            DocumentSetEntry.objects.create(document_set=document_set, doc="rfc2119")
        self.assertEqual(self.pending(), ["rfc2119"])

    def test_an_rfc_subscription_marks_its_document(self):
        with self.captureOnCommitCallbacks(execute=True):
            Subscription.objects.create(
                user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
            )
        self.assertEqual(self.pending(), ["rfc9110"])

    def test_other_subscription_kinds_mark_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            Subscription.objects.create(user=self.user, kind=Subscription.Kind.NEW_RFC)
        self.assertEqual(self.pending(), [])

    def test_a_rolled_back_rating_marks_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            with contextlib.suppress(RuntimeError), transaction.atomic():
                Rating.objects.create(rfc="rfc9110", user=self.user, value=4)
                raise RuntimeError("rolled back")
        self.assertEqual(self.pending(), [])


@override_settings(
    REEF_TRIGGER_RED_PRECOMPUTE_URL="http://el-precompute-multiple.red/",
    REEF_DOCUMENT_CHANGE_QUIET_SECONDS=60,
    REEF_RED_PRECOMPUTE_BATCH_SIZE=2,
)
class PushDocumentChangesTests(TestCase):
    """Marked documents are republished and Red is told, once the writes go quiet."""

    def setUp(self):
        run = mock.patch("precomputer.tasks._run", return_value=True)
        self.run = run.start()
        self.addCleanup(run.stop)
        post = mock.patch("precomputer.tasks._post_json")
        self.post = post.start()
        self.addCleanup(post.stop)

    def mark(self, *docs, seconds_ago=120):
        seen = timezone.now() - datetime.timedelta(seconds=seconds_ago)
        for doc in docs:
            row = PendingDocumentChange.objects.create(doc=doc)
            PendingDocumentChange.objects.filter(pk=row.pk).update(
                first_seen=seen, last_seen=seen
            )

    def test_nothing_pending_does_nothing(self):
        self.assertFalse(push_document_changes())
        self.run.assert_not_called()
        self.post.assert_not_called()

    def test_a_document_still_being_written_to_waits(self):
        self.mark("rfc9110", seconds_ago=5)
        self.assertFalse(push_document_changes())
        self.run.assert_not_called()
        self.assertEqual(PendingDocumentChange.objects.count(), 1)

    def test_quiet_documents_are_republished_and_red_is_told(self):
        self.mark("rfc9110", "rfc2119", "bcp14")
        self.assertTrue(push_document_changes())
        self.run.assert_called_once_with(
            "stats", "ratings", docs=["rfc9110", "rfc2119", "bcp14"]
        )
        # bcp14 is republished but has no page on Red, so Red is not told about it.
        self.assertEqual(
            self.post.call_args.args,
            (
                "http://el-precompute-multiple.red/",
                {"rfcs": "9110,2119", "skipIndices": "true"},
            ),
        )
        self.assertEqual(PendingDocumentChange.objects.count(), 0)

    def test_red_is_told_in_batches(self):
        self.mark("rfc1", "rfc2", "rfc3")
        push_document_changes()
        self.assertEqual(
            [call.args[1]["rfcs"] for call in self.post.call_args_list],
            ["1,2", "3"],
        )

    @override_settings(REEF_TRIGGER_RED_PRECOMPUTE_URL="")
    def test_without_a_listener_reef_still_publishes(self):
        self.mark("rfc9110")
        self.assertTrue(push_document_changes())
        self.run.assert_called_once()
        self.post.assert_not_called()
        self.assertEqual(PendingDocumentChange.objects.count(), 0)

    def test_a_failed_republish_keeps_the_rows(self):
        self.run.return_value = False
        self.mark("rfc9110")
        self.assertFalse(push_document_changes())
        self.post.assert_not_called()
        self.assertEqual(PendingDocumentChange.objects.count(), 1)

    def test_a_failed_notification_keeps_the_rows(self):
        self.post.side_effect = OSError("listener down")
        self.mark("rfc9110")
        self.assertFalse(push_document_changes())
        self.assertEqual(PendingDocumentChange.objects.count(), 1)

    def test_a_document_written_to_during_the_run_stays_pending(self):
        self.mark("rfc9110")

        def write_again(*args, **kwargs):
            PendingDocumentChange.objects.filter(doc="rfc9110").update(
                last_seen=timezone.now() + datetime.timedelta(seconds=1)
            )
            return True

        self.run.side_effect = write_again
        self.assertTrue(push_document_changes())
        self.assertEqual(PendingDocumentChange.objects.count(), 1)
