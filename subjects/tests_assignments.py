# Copyright The IETF Trust 2026, All Rights Reserved
"""Importing assignments against a vocabulary that already exists."""

import csv
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from subjects.models import Subject, SubjectAssignment

COLUMNS = ["rfc", "title", "year", "tags", "full_paths"]


def sheet(directory, rows):
    path = Path(directory) / "assignments.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for doc, tags, *rest in rows:
            writer.writerow(
                {
                    "rfc": doc,
                    "tags": tags,
                    "full_paths": rest[0] if rest else "",
                    "title": "",
                    "year": "",
                }
            )
    return str(path)


class ImportAssignmentsTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.messaging = Subject.objects.create(name="Messaging", slug="messaging")
        self.email = Subject.objects.create(
            name="Email", slug="email", parent=self.messaging
        )

    def run_import(self, rows, **options):
        out, err = StringIO(), StringIO()
        call_command(
            "import_assignments",
            sheet(self.tmp, rows),
            stdout=out,
            stderr=err,
            **{"write": True, **options},
        )
        return out.getvalue(), err.getvalue()

    def docs_on(self, subject):
        return sorted(subject.assignments.values_list("doc", flat=True))

    def test_it_files_a_document_under_every_tag_on_its_row(self):
        self.run_import([("RFC 5322", "messaging;email")])
        self.assertEqual(self.docs_on(self.messaging), ["rfc5322"])
        self.assertEqual(self.docs_on(self.email), ["rfc5322"])

    def test_identifiers_are_canonicalised(self):
        self.run_import([("RFC 5322", "email"), ("rfc9110", "email")])
        self.assertEqual(self.docs_on(self.email), ["rfc5322", "rfc9110"])

    def test_it_resolves_by_tag_and_ignores_the_sheet_s_own_path(self):
        """The two sheets can disagree about where a subject sits without
        disagreeing about which subjects exist, and the vocabulary is what decides
        placement. Here the sheet files email under a root that does not exist."""
        self.run_import([("rfc5322", "email", "web / email")])
        self.assertEqual(self.docs_on(self.email), ["rfc5322"])
        self.assertFalse(Subject.all_objects.filter(slug="web").exists())

    def test_it_creates_no_subjects(self):
        before = set(Subject.all_objects.values_list("slug", flat=True))
        self.run_import([("rfc5322", "email")])
        self.assertEqual(
            set(Subject.all_objects.values_list("slug", flat=True)), before
        )

    def test_an_unknown_tag_stops_the_run(self):
        # Documents would otherwise lose a subject somebody wrote down, silently
        # and in bulk.
        with self.assertRaises(CommandError) as caught:
            self.run_import([("rfc5322", "email;nonesuch")])
        self.assertIn("name no subject", str(caught.exception))
        self.assertFalse(SubjectAssignment.objects.exists())

    def test_skip_unknown_imports_the_rest_and_says_what_it_dropped(self):
        _, err = self.run_import([("rfc5322", "email;nonesuch")], skip_unknown=True)
        self.assertIn("nonesuch", err)
        self.assertEqual(self.docs_on(self.email), ["rfc5322"])

    def test_a_repeated_pair_is_one_assignment(self):
        # A sheet naming the same subject twice on one row asserts one thing twice.
        out, _ = self.run_import([("rfc5322", "email;email")])
        self.assertIn("1 assignment", out)
        self.assertEqual(self.docs_on(self.email), ["rfc5322"])

    def test_running_it_again_adds_nothing(self):
        rows = [("rfc5322", "email"), ("rfc9110", "messaging")]
        self.run_import(rows)
        out, _ = self.run_import(rows)
        self.assertIn("0 created, 2 already there", out)
        self.assertEqual(SubjectAssignment.objects.count(), 2)

    def test_a_dry_run_writes_nothing(self):
        out, _ = self.run_import([("rfc5322", "email")], write=False)
        self.assertIn("1 created", out)
        self.assertIn("rolled back", out)
        self.assertFalse(SubjectAssignment.objects.exists())

    def test_an_unusable_identifier_is_reported_and_the_rest_imported(self):
        _, err = self.run_import([("not-a-doc", "email"), ("rfc5322", "email")])
        self.assertIn("not-a-doc", err)
        self.assertEqual(self.docs_on(self.email), ["rfc5322"])

    def test_a_retired_subject_takes_no_assignments(self):
        """Retired means not offered. A sheet still naming it is stale rather than
        an instruction to file documents under something nobody can reach."""
        self.email.retire()
        with self.assertRaises(CommandError):
            self.run_import([("rfc5322", "email")])
