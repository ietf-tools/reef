# Copyright The IETF Trust 2026, All Rights Reserved
"""Loading a curated sheet, and refusing to load a broken one."""

import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase

from .models import Subject, SubjectAssignment

HEADER = "rfc,title,year,tags,full_paths\n"


def sheet(*rows):
    """Write a CSV in the shape of the draft assignment sheet."""
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, newline="", encoding="utf-8"
    )
    handle.write(HEADER)
    for row in rows:
        handle.write(row + "\n")
    handle.close()
    return handle.name


def row(rfc, tags, paths, title="A Title", year="2026"):
    return f'{rfc},"{title}",{year},{tags},"{paths}"'


class ImportTestCase(TestCase):
    def run_import(self, path, *args):
        out, err = StringIO(), StringIO()
        call_command("import_subjects", path, *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()


class ImportTests(ImportTestCase):
    def test_a_dry_run_writes_nothing(self):
        path = sheet(row("RFC6376", "dkim", "messaging / email / dkim"))
        out, _ = self.run_import(path)
        self.assertIn("Dry run", out)
        self.assertEqual(Subject.all_objects.count(), 0)

    def test_writing_creates_the_whole_path(self):
        path = sheet(row("RFC6376", "dkim", "messaging / email / dkim"))
        self.run_import(path, "--write")
        self.assertEqual(
            sorted(Subject.all_objects.values_list("path", flat=True)),
            ["messaging", "messaging/email", "messaging/email/dkim"],
        )

    def test_ancestors_are_created_even_when_nothing_is_filed_on_them(self):
        # The reason a branch exists at all: messaging holds nothing itself.
        path = sheet(row("RFC6376", "dkim", "messaging / email / dkim"))
        self.run_import(path, "--write")
        messaging = Subject.all_objects.get(slug="messaging")
        self.assertEqual(messaging.assignments.count(), 0)
        self.assertEqual(messaging.depth, 0)

    def test_names_are_placeholders_derived_from_the_slug(self):
        path = sheet(row("RFC6376", "dkim", "messaging / email-authentication"))
        self.run_import(path, "--write")
        self.assertEqual(
            Subject.all_objects.get(slug="email-authentication").name,
            "Email Authentication",
        )

    def test_the_document_is_assigned_to_the_leaf_only(self):
        path = sheet(row("RFC6376", "dkim", "messaging / email / dkim"))
        self.run_import(path, "--write")
        self.assertEqual(
            [a.subject.slug for a in SubjectAssignment.objects.all()], ["dkim"]
        )

    def test_several_paths_on_one_row_are_all_applied(self):
        path = sheet(
            row("RFC18", "arpanet;ncp", "link-layer / arpanet | transport / ncp")
        )
        self.run_import(path, "--write")
        self.assertEqual(
            sorted(SubjectAssignment.objects.values_list("subject__path", flat=True)),
            ["link-layer/arpanet", "transport/ncp"],
        )

    def test_identifiers_are_canonicalised(self):
        path = sheet(row("RFC 6376", "dkim", "messaging / dkim"))
        self.run_import(path, "--write")
        self.assertEqual(SubjectAssignment.objects.get().doc, "rfc6376")

    def test_importing_twice_creates_nothing_the_second_time(self):
        path = sheet(row("RFC6376", "dkim", "messaging / email / dkim"))
        self.run_import(path, "--write")
        out, _ = self.run_import(path, "--write")
        self.assertIn("0 created", out)
        self.assertEqual(SubjectAssignment.objects.count(), 1)

    def test_it_joins_a_vocabulary_that_already_exists(self):
        Subject.objects.create(slug="messaging", name="Messaging")
        path = sheet(row("RFC6376", "dkim", "messaging / email / dkim"))
        self.run_import(path, "--write")
        self.assertEqual(Subject.all_objects.get(slug="email").parent.slug, "messaging")


class RefusalTests(ImportTestCase):
    def test_a_slug_under_two_paths_is_a_hard_failure(self):
        """The invariant the whole arrangement rests on.

        Slugs are globally unique, so one of the two would win silently and the
        documents filed under the other would land in the wrong branch.
        """
        path = sheet(
            row("RFC1", "send", "internet-layer / send"),
            row("RFC779", "send", "applications / telnet / send"),
        )
        with self.assertRaises(CommandError) as caught:
            self.run_import(path, "--write")
        self.assertIn("globally unique", str(caught.exception))
        self.assertEqual(Subject.all_objects.count(), 0)

    def test_a_path_deeper_than_the_ceiling_is_reported_and_skipped(self):
        path = sheet(row("RFC1", "e", "a / b / c / d / e"))
        _, err = self.run_import(path, "--write")
        self.assertIn("deeper than 4 levels", err)
        self.assertEqual(Subject.all_objects.count(), 0)

    def test_a_bare_number_is_reported_and_skipped(self):
        path = sheet(row("6376", "dkim", "messaging / dkim"))
        _, err = self.run_import(path, "--write")
        self.assertIn("6376", err)
        self.assertEqual(SubjectAssignment.objects.count(), 0)

    def test_a_missing_column_is_refused(self):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8"
        )
        handle.write("rfc,title\nRFC1,A\n")
        handle.close()
        with self.assertRaises(CommandError) as caught:
            self.run_import(handle.name, "--write")
        self.assertIn("full_paths", str(caught.exception))

    def test_a_file_that_is_not_there_is_refused(self):
        with self.assertRaises(CommandError):
            self.run_import(str(Path(tempfile.gettempdir()) / "nope.csv"))

    def test_a_row_with_no_paths_is_skipped_quietly(self):
        path = sheet(row("RFC1", "", ""))
        self.run_import(path, "--write")
        self.assertEqual(Subject.all_objects.count(), 0)


class ReportTests(ImportTestCase):
    def test_the_report_counts_the_branches_that_carry_nothing(self):
        path = sheet(row("RFC6376", "dkim", "messaging / email / dkim"))
        out, _ = self.run_import(path)
        self.assertIn("3 subject(s) named by the sheet", out)
        self.assertIn("2 of those carry nothing directly", out)

    def test_the_report_counts_the_assignments(self):
        path = sheet(
            row("RFC6376", "dkim", "messaging / dkim"),
            row("RFC5321", "smtp", "messaging / smtp"),
        )
        out, _ = self.run_import(path)
        self.assertIn("2 assignment(s) in the sheet", out)
