# Copyright The IETF Trust 2026, All Rights Reserved
"""Seeding the vocabulary from the curated sheet.

The sheet committed in subjects/seed/ is tested here too, not just the command
that reads it. It is data somebody edits by hand, the failures it can carry are
the ones the command refuses on, and nothing else would notice until a seed run
failed in front of whoever was doing it.
"""

import csv
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from subjects.management.commands.seed_subjects import DEFAULT_SHEET
from subjects.models import MAX_DEPTH, Subject

COLUMNS = [
    "tag",
    "subject tag title",
    "kind",
    "level",
    "parent",
    "path",
    "description",
    "direct documents",
    "total documents",
]


def sheet(tmp_path, rows):
    """A sheet with the columns the real one has, so the command reads it the same."""
    path = Path(tmp_path) / "sheet.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in COLUMNS})
    return str(path)


def row(tag, title=None, parent="", path=None, description=""):
    return {
        "tag": tag,
        "subject tag title": title if title is not None else tag.title(),
        "parent": parent,
        "path": path if path is not None else "/" + tag,
        "description": description,
        "kind": "topic",
        "level": "1",
    }


class SeedCommandTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def seed(self, rows, write=True):
        out, err = StringIO(), StringIO()
        call_command(
            "seed_subjects", sheet(self.tmp, rows), write=write, stdout=out, stderr=err
        )
        return out.getvalue()

    def test_it_creates_the_vocabulary_with_its_curated_names(self):
        """The reason this command exists rather than letting import_subjects make
        the subjects: that one has only a slug to work from and title-cases it."""
        self.seed(
            [
                row("messaging", "Messaging", description="Mail and the like."),
                row("email", "Email", parent="messaging", path="/messaging/email"),
            ]
        )
        email = Subject.objects.get(slug="email")
        self.assertEqual(email.name, "Email")
        self.assertEqual(email.parent.slug, "messaging")
        self.assertEqual(email.path, "messaging/email")
        # Zero-based: depth counts separators, so a root is 0. The sheet's `level`
        # column is one-based and is not what this is, which is why nothing reads it.
        self.assertEqual(email.depth, 1)
        self.assertEqual(
            Subject.objects.get(slug="messaging").description, "Mail and the like."
        )

    def test_running_it_again_changes_nothing(self):
        rows = [row("messaging", "Messaging"), row("web", "Web")]
        self.seed(rows)
        self.assertIn("0 created, 0 updated, 2 unchanged", self.seed(rows))

    def test_an_edited_sheet_updates_what_it_changed(self):
        self.seed([row("messaging", "Messaging")])
        report = self.seed([row("messaging", "Messaging and Mail", description="New.")])
        self.assertIn("0 created, 1 updated", report)
        subject = Subject.objects.get(slug="messaging")
        self.assertEqual(subject.name, "Messaging and Mail")
        self.assertEqual(subject.description, "New.")

    def test_moving_a_subject_moves_its_descendants(self):
        """Through save() rather than a bulk update, which is what recomputes the
        paths underneath."""
        self.seed(
            [
                row("messaging", "Messaging"),
                row("web", "Web"),
                row("email", "Email", parent="messaging", path="/messaging/email"),
                row("dkim", "DKIM", parent="email", path="/messaging/email/dkim"),
            ]
        )
        self.seed(
            [
                row("messaging", "Messaging"),
                row("web", "Web"),
                row("email", "Email", parent="web", path="/web/email"),
                row("dkim", "DKIM", parent="email", path="/web/email/dkim"),
            ]
        )
        self.assertEqual(Subject.objects.get(slug="dkim").path, "web/email/dkim")

    def test_a_dry_run_writes_nothing(self):
        report = self.seed([row("messaging", "Messaging")], write=False)
        self.assertIn("1 created", report)
        self.assertIn("rolled back", report)
        self.assertFalse(Subject.all_objects.exists())

    def test_a_retired_subject_is_updated_rather_than_duplicated(self):
        """And stays retired: retiring is a decision made in Reef, and a sheet
        that still lists the subject is not an instruction to reverse it."""
        self.seed([row("messaging", "Messaging")])
        Subject.objects.get(slug="messaging").retire()
        self.seed([row("messaging", "Messaging Renamed")])
        self.assertEqual(Subject.all_objects.filter(slug="messaging").count(), 1)
        subject = Subject.all_objects.get(slug="messaging")
        self.assertTrue(subject.is_retired)
        self.assertEqual(subject.name, "Messaging Renamed")


class SheetValidationTests(TestCase):
    """What the command refuses, and refuses before writing any of it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def refuses(self, rows):
        with self.assertRaises(CommandError) as caught:
            call_command(
                "seed_subjects",
                sheet(self.tmp, rows),
                write=True,
                stdout=StringIO(),
                stderr=StringIO(),
            )
        self.assertFalse(Subject.all_objects.exists())
        return str(caught.exception)

    def test_a_repeated_slug(self):
        # Slugs are globally unique because the detail URL carries no path, so a
        # repeat would make one of the two unreachable.
        self.assertIn("1 problem", self.refuses([row("web"), row("web")]))

    def test_a_parent_the_sheet_does_not_name(self):
        self.assertIn("1 problem", self.refuses([row("email", parent="messaging")]))

    def test_a_cycle(self):
        self.assertIn(
            "problem",
            self.refuses(
                [
                    row("a", parent="b", path="/b/a"),
                    row("b", parent="a", path="/a/b"),
                ]
            ),
        )

    def test_a_path_that_disagrees_with_the_parents(self):
        # The sheet says both, so the two can contradict each other, and neither
        # half is authoritative enough to silently win.
        self.assertIn(
            "1 problem",
            self.refuses(
                [row("messaging"), row("email", parent="messaging", path="/web/email")]
            ),
        )

    def test_a_subject_deeper_than_the_ceiling(self):
        # One chain a level too long. Only the last row is the problem, so this
        # also shows the depth check naming the row rather than the sheet.
        rows = []
        for index in range(MAX_DEPTH + 1):
            slugs = [f"s{n}" for n in range(index + 1)]
            rows.append(
                row(
                    slugs[-1],
                    parent=slugs[-2] if index else "",
                    path="/" + "/".join(slugs),
                )
            )
        self.assertIn("1 problem", self.refuses(rows))

    def test_every_problem_is_reported_at_once(self):
        # A sheet with three faults should be fixed once, not three times.
        self.assertIn(
            "3 problem",
            self.refuses(
                [row("web"), row("web"), row("a", parent="nobody"), row("b", title="")]
            ),
        )


class CommittedSheetTests(TestCase):
    """The sheet in the repository, checked as data rather than as an example."""

    def test_it_seeds(self):
        out = StringIO()
        call_command("seed_subjects", write=True, stdout=out, stderr=StringIO())
        self.assertIn("created", out.getvalue())
        self.assertEqual(Subject.objects.count(), self.sheet_rows())

    def test_every_stored_path_matches_the_one_derived_from_its_parents(self):
        call_command("seed_subjects", write=True, stdout=StringIO(), stderr=StringIO())
        for subject in Subject.objects.all():
            self.assertEqual(subject.path, subject.derived_path)
            self.assertLessEqual(subject.depth, MAX_DEPTH)

    def sheet_rows(self):
        with open(DEFAULT_SHEET, newline="", encoding="utf-8") as handle:
            return len(list(csv.DictReader(handle)))
