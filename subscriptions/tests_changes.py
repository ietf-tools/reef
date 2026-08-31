# Copyright The IETF Trust 2026, All Rights Reserved
"""Detecting what changed about the RFC series between two readings of Red's index."""

import datetime
import json
import zlib
from unittest import mock

from django.test import TestCase, override_settings

from reef import rfcmeta
from reef.testing import stub_rfc_index
from subscriptions.changes import (
    WATCHED_FIELDS,
    DocumentChange,
    as_event,
    detect,
    diff,
    load_snapshot,
    reduce_index,
    render_change,
    save_snapshot,
)
from subscriptions.models import DocumentSnapshot
from subscriptions.tasks import detect_rfc_changes


def meta(**overrides):
    """One document as rfcmeta reduces it."""
    return {
        "title": "HTTP Semantics",
        "subseries": [],
        "status": "ps",
        "status_name": "proposed standard",
        "obsoleted_by": [],
        "updates": [],
        "updated_by": [],
        **overrides,
    }


class SnapshotStorageTests(TestCase):
    def test_there_is_nothing_before_the_first_run(self):
        self.assertIsNone(load_snapshot())

    def test_a_saved_snapshot_reads_back(self):
        reduced = {"rfc9110": {name: meta()[name] for name in WATCHED_FIELDS}}
        save_snapshot(reduced, datetime.date(2026, 8, 31))
        self.assertEqual(load_snapshot(), reduced)

    def test_saving_twice_replaces_rather_than_accumulates(self):
        save_snapshot({"rfc1": {}}, datetime.date(2026, 8, 30))
        save_snapshot({"rfc2": {}}, datetime.date(2026, 8, 31))
        self.assertEqual(DocumentSnapshot.objects.count(), 1)
        self.assertEqual(load_snapshot(), {"rfc2": {}})

    def test_the_payload_is_compressed(self):
        save_snapshot({"rfc9110": {"status": "ps"}}, None)
        row = DocumentSnapshot.objects.get()
        self.assertEqual(
            json.loads(zlib.decompress(bytes(row.payload))),
            {"rfc9110": {"status": "ps"}},
        )

    def test_an_unreadable_payload_reads_as_absent(self):
        """Which makes the next run a seeding run: it sends nothing and writes a good
        snapshot, rather than treating every document as new."""
        DocumentSnapshot.objects.create(pk=1, payload=b"not compressed")
        with self.assertLogs("reef", level="ERROR"):
            self.assertIsNone(load_snapshot())

    def test_only_the_watched_fields_are_kept(self):
        """Title is not among them, so a correction to one is not a change."""
        reduced = reduce_index({"rfc9110": meta()})
        self.assertEqual(set(reduced["rfc9110"]), set(WATCHED_FIELDS))
        self.assertNotIn("title", reduced["rfc9110"])


class DiffTests(TestCase):
    def test_a_document_that_appears_is_new(self):
        changes = diff({}, reduce_index({"rfc9110": meta()}))
        self.assertEqual(len(changes), 1)
        self.assertTrue(changes[0].is_new)
        self.assertEqual(changes[0].doc, "rfc9110")
        self.assertEqual(changes[0].fields, {})

    def test_an_unchanged_document_is_not_reported(self):
        before = reduce_index({"rfc9110": meta()})
        self.assertEqual(diff(before, dict(before)), [])

    def test_a_status_change_is_reported_with_both_values(self):
        before = reduce_index({"rfc9110": meta(status="ps")})
        after = reduce_index({"rfc9110": meta(status="hist")})
        change = diff(before, after)[0]
        self.assertFalse(change.is_new)
        self.assertEqual(change.fields, {"status": ("ps", "hist")})

    def test_gaining_an_obsoleted_by_entry_is_reported(self):
        before = reduce_index({"rfc9110": meta()})
        after = reduce_index({"rfc9110": meta(obsoleted_by=[9999])})
        self.assertEqual(diff(before, after)[0].fields, {"obsoleted_by": ([], [9999])})

    def test_a_subseries_change_is_reported(self):
        """Which is what makes a change to a subseries' constitution detectable."""
        before = reduce_index({"rfc2119": meta(subseries=[])})
        after = reduce_index({"rfc2119": meta(subseries=["bcp14"])})
        self.assertEqual(diff(before, after)[0].fields, {"subseries": ([], ["bcp14"])})

    def test_a_title_change_alone_is_not_a_change(self):
        """A typo correction must not mail everybody tracking the document."""
        before = reduce_index({"rfc9110": meta(title="HTTP Semantcs")})
        after = reduce_index({"rfc9110": meta(title="HTTP Semantics")})
        self.assertEqual(diff(before, after), [])

    def test_several_fields_moving_is_one_change(self):
        """A document obsoleted and made historic in one publication is one line."""
        before = reduce_index({"rfc9110": meta()})
        after = reduce_index({"rfc9110": meta(status="hist", obsoleted_by=[9999])})
        changes = diff(before, after)
        self.assertEqual(len(changes), 1)
        self.assertEqual(set(changes[0].fields), {"status", "obsoleted_by"})

    def test_a_document_vanishing_is_not_reported(self):
        """Red does not unpublish RFCs, so a disappearance is a failed build rather
        than news, and reporting it would turn one bad upstream run into mail."""
        before = reduce_index({"rfc9110": meta(), "rfc8446": meta()})
        after = reduce_index({"rfc9110": meta()})
        self.assertEqual(diff(before, after), [])

    def test_changes_come_back_in_document_order(self):
        before = {}
        after = reduce_index(
            {"rfc10": meta(), "rfc9": meta(), "bcp14": meta(), "rfc100": meta()}
        )
        self.assertEqual(
            [c.doc for c in diff(before, after)], ["bcp14", "rfc9", "rfc10", "rfc100"]
        )

    def test_a_change_knows_how_to_name_its_document(self):
        change = diff({}, reduce_index({"rfc9110": meta()}))[0]
        self.assertEqual(change.doc_display, "RFC 9110")


class DetectTests(TestCase):
    def setUp(self):
        stub_rfc_index(self, {"rfc9110": meta()})

    def rewarm(self, mapping, created_on=None):
        rfcmeta._memo["value"] = (mapping, created_on)
        rfcmeta._memo["expires"] = float("inf")

    def test_the_first_run_seeds_and_reports_nothing(self):
        with self.assertLogs("reef", level="WARNING") as logs:
            result = detect()
        self.assertEqual(result.changes, [])
        self.assertIn("seeding", "\n".join(logs.output))
        result.save()
        self.assertEqual(load_snapshot(), reduce_index({"rfc9110": meta()}))

    def test_a_second_run_with_a_changed_index_reports_it(self):
        detect().save()
        self.rewarm({"rfc9110": meta(status="hist")}, datetime.date(2026, 9, 1))
        result = detect()
        self.assertEqual(len(result.changes), 1)
        self.assertEqual(result.changes[0].fields, {"status": ("ps", "hist")})

    def test_a_run_against_an_unrepublished_index_compares_nothing(self):
        """Red rebuilds when RFCs are published, so this is the ordinary quiet case."""
        self.rewarm({"rfc9110": meta()}, datetime.date(2026, 8, 31))
        detect().save()
        self.rewarm({"rfc9110": meta(status="hist")}, datetime.date(2026, 8, 31))
        with self.assertLogs("reef", level="INFO") as logs:
            result = detect()
        self.assertEqual(result.changes, [])
        self.assertIn("has not republished", "\n".join(logs.output))

    def test_no_index_means_no_detection_rather_than_an_empty_diff(self):
        """An empty diff would be indistinguishable from Red being fine and quiet;
        worse, treating an absent index as an empty series would report every
        document as vanished."""
        with mock.patch("reef.rfcmeta.get_index", return_value=None):
            with self.assertLogs("reef", level="ERROR"):
                self.assertIsNone(detect())

    def test_saving_advances_to_the_reading_the_changes_came_from(self):
        """Not to whatever Red is serving by the time the run finishes."""
        self.rewarm({"rfc9110": meta()}, datetime.date(2026, 8, 31))
        result = detect()
        self.rewarm({"rfc9110": meta(status="hist")}, datetime.date(2026, 9, 1))
        result.save()
        self.assertEqual(
            DocumentSnapshot.objects.get().created_on, datetime.date(2026, 8, 31)
        )


class DetectTaskTests(TestCase):
    """The daily task. It advances the snapshot but does not notify anybody yet."""

    def setUp(self):
        stub_rfc_index(self, {"rfc9110": meta()})

    def rewarm(self, mapping, created_on=None):
        rfcmeta._memo["value"] = (mapping, created_on)
        rfcmeta._memo["expires"] = float("inf")

    def test_the_first_run_seeds_and_reports_nothing(self):
        with self.assertLogs("reef", level="WARNING"):
            self.assertEqual(detect_rfc_changes(), 0)
        self.assertIsNotNone(load_snapshot())

    def test_a_change_is_counted_and_logged(self):
        detect_rfc_changes()
        self.rewarm({"rfc9110": meta(status="hist")}, datetime.date(2026, 9, 1))
        with self.assertLogs("reef", level="INFO") as logs:
            self.assertEqual(detect_rfc_changes(), 1)
        self.assertIn("RFC 9110", "\n".join(logs.output))

    def test_a_change_is_reported_once_and_not_again(self):
        """The snapshot advancing is what makes the run idempotent day to day."""
        detect_rfc_changes()
        self.rewarm({"rfc9110": meta(status="hist")}, datetime.date(2026, 9, 1))
        self.assertEqual(detect_rfc_changes(), 1)
        self.rewarm({"rfc9110": meta(status="hist")}, datetime.date(2026, 9, 2))
        self.assertEqual(detect_rfc_changes(), 0)

    def test_red_being_unreachable_leaves_the_snapshot_alone(self):
        """So the next run compares against the same reading and misses nothing."""
        detect_rfc_changes()
        before = load_snapshot()
        with mock.patch("reef.rfcmeta.get_index", return_value=None):
            with self.assertLogs("reef", level="ERROR"):
                self.assertEqual(detect_rfc_changes(), 0)
        self.assertEqual(load_snapshot(), before)


class RenderChangeTests(TestCase):
    """The sentence a digest shows, which Reef composes because the feed the
    templates were written against does not exist."""

    def index(self, mapping=None):
        return rfcmeta.DocumentIndex(
            mapping if mapping is not None else {"rfc9110": meta()}, None
        )

    def render(self, before, after, doc="rfc9110", mapping=None):
        change = diff(reduce_index(before), reduce_index(after))[0]
        return render_change(change, self.index(mapping or after))

    def test_a_new_document_names_its_status(self):
        self.assertEqual(
            self.render({}, {"rfc9110": meta(status_name="proposed standard")}),
            "Published as proposed standard.",
        )

    def test_a_new_document_with_no_status_still_reads(self):
        self.assertEqual(
            self.render({}, {"rfc9110": meta(status=None, status_name=None)}),
            "Published.",
        )

    def test_a_status_change_uses_the_name_red_gives_it(self):
        """Rather than the slug the snapshot stores."""
        self.assertEqual(
            self.render(
                {"rfc9110": meta(status="ps", status_name="proposed standard")},
                {"rfc9110": meta(status="hist", status_name="historic")},
            ),
            "Status changed to historic.",
        )

    def test_being_obsoleted_names_the_document(self):
        self.assertEqual(
            self.render({"rfc9110": meta()}, {"rfc9110": meta(obsoleted_by=[9999])}),
            "Obsoleted by RFC 9999.",
        )

    def test_two_obsoleting_documents_read_as_prose(self):
        self.assertEqual(
            self.render(
                {"rfc9110": meta()}, {"rfc9110": meta(obsoleted_by=[7230, 7231])}
            ),
            "Obsoleted by RFC 7230 and RFC 7231.",
        )

    def test_being_updated_names_the_document(self):
        self.assertEqual(
            self.render({"rfc9110": meta()}, {"rfc9110": meta(updated_by=[9999])}),
            "Updated by RFC 9999.",
        )

    def test_joining_a_subseries(self):
        self.assertEqual(
            self.render({"rfc2119": meta()}, {"rfc2119": meta(subseries=["bcp14"])}),
            "Added to BCP 14.",
        )

    def test_leaving_a_subseries(self):
        self.assertEqual(
            self.render({"rfc2119": meta(subseries=["bcp14"])}, {"rfc2119": meta()}),
            "Removed from BCP 14.",
        )

    def test_several_facts_become_one_sentence(self):
        """A document obsoleted and made historic in one publication is one line."""
        self.assertEqual(
            self.render(
                {"rfc9110": meta(status="ps")},
                {
                    "rfc9110": meta(
                        status="hist", status_name="historic", obsoleted_by=[9999]
                    )
                },
            ),
            "Status changed to historic; Obsoleted by RFC 9999.",
        )

    def test_a_relation_losing_an_entry_names_what_it_lost(self):
        """Red correcting a mistaken "obsoleted by" is not news about the document,
        but it is describable, and naming it beats a vague catch-all."""
        self.assertEqual(
            self.render({"rfc9110": meta(obsoleted_by=[9999])}, {"rfc9110": meta()}),
            "No longer obsoleted by RFC 9999.",
        )

    def test_a_relation_gaining_and_losing_reads_as_both(self):
        self.assertEqual(
            self.render(
                {"rfc9110": meta(obsoleted_by=[9998])},
                {"rfc9110": meta(obsoleted_by=[9999])},
            ),
            "Obsoleted by RFC 9999; No longer obsoleted by RFC 9998.",
        )

    def test_no_longer_updating_reads(self):
        self.assertEqual(
            self.render({"rfc9110": meta(updates=[7230])}, {"rfc9110": meta()}),
            "No longer updates RFC 7230.",
        )

    def test_an_undescribable_change_names_the_fields_that_moved(self):
        """Reachable only if a value changes shape at Red. Naming the field lets a
        reader go and look, which "record corrected" alone does not."""
        change = DocumentChange(
            doc="rfc9110", fields={"obsoleted_by": ("was a string", "now another")}
        )
        self.assertEqual(
            render_change(change, self.index()),
            "Record corrected: obsoleting documents.",
        )

    def test_a_document_missing_from_the_index_still_renders(self):
        change = diff({}, reduce_index({"rfc9110": meta()}))[0]
        self.assertEqual(render_change(change, self.index({})), "Published.")


class EventShapeTests(TestCase):
    """The shape delivery already takes, unchanged by where the events now come
    from."""

    def test_an_event_carries_what_the_template_reads(self):
        change = diff({}, reduce_index({"rfc9110": meta()}))[0]
        index = rfcmeta.DocumentIndex({"rfc9110": meta()}, None)
        self.assertEqual(
            as_event(change, index),
            {
                "doc": "rfc9110",
                "doc_display": "RFC 9110",
                "change": "Published as proposed standard.",
                "url": "https://www.rfc-editor.org/info/rfc9110/",
            },
        )

    def test_the_url_is_the_canonical_one(self):
        """/info/<doc>/ rather than /rfc/<doc>, which 302s to it: a notification is
        read long after it is sent and should not spend a redirect."""
        change = diff({}, reduce_index({"rfc2119": meta()}))[0]
        self.assertTrue(change.url.endswith("/info/rfc2119/"))

    @override_settings(REEF_RFC_SITE_URL="https://red.example.org/")
    def test_a_trailing_slash_on_the_site_url_does_not_double_up(self):
        change = diff({}, reduce_index({"rfc2119": meta()}))[0]
        self.assertEqual(change.url, "https://red.example.org/info/rfc2119/")
