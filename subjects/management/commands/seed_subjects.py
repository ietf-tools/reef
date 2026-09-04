# Copyright The IETF Trust 2026, All Rights Reserved
"""Load the curated subject vocabulary from the sheet in subjects/seed/.

The companion to import_subjects, and deliberately a separate command, because
the two read different sheets and answer different questions. This one is the
vocabulary: one row per subject, carrying the name and description somebody
wrote. import_subjects is the assignments: one row per document, carrying the
paths it is filed under. Neither is derivable from the other -- a subject with
nothing filed on it still belongs in the vocabulary, and a document's assignment
says nothing about what its subject should be called.

Running them in that order is what gets both. import_subjects creates any path
its sheet names that does not exist yet, with a mechanically title-cased slug for
a name, and skips the paths that do; so seeding first means the curated names and
descriptions are the ones that survive.

Three columns in the sheet have no home here and are read only to be checked
against what they imply. `kind`, which sorts a subject into a topic or a
technology, is a distinction Reef's model does not draw; `direct documents` and
`total documents` are counts of assignments this sheet does not carry, which Reef
derives from the assignments it does hold. They stay in the file because the file
is the curated artifact rather than an import format, and dropping a column on
the way in is easier to undo than dropping it on the way to the repository.

Dry run unless --write is given, as import_subjects is: this rewrites the name
and description of every subject in the vocabulary, which is a curation act and
should have to be confirmed.
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from subjects.models import MAX_DEPTH, PATH_SEPARATOR, Subject

DEFAULT_SHEET = Path(__file__).resolve().parents[2] / "seed" / "rfc-subject-tags.csv"

SLUG_COLUMN = "tag"
NAME_COLUMN = "subject tag title"
PARENT_COLUMN = "parent"
PATH_COLUMN = "path"
DESCRIPTION_COLUMN = "description"
REQUIRED_COLUMNS = (
    SLUG_COLUMN,
    NAME_COLUMN,
    PARENT_COLUMN,
    PATH_COLUMN,
    DESCRIPTION_COLUMN,
)


class Command(BaseCommand):
    help = "Seed the subject vocabulary from the curated sheet."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_path",
            nargs="?",
            default=str(DEFAULT_SHEET),
            help=f"The sheet to read. Defaults to {DEFAULT_SHEET.name} in the repo.",
        )
        parser.add_argument(
            "--write",
            action="store_true",
            help="Actually write. Without it, nothing is kept and the report is "
            "what would have happened.",
        )

    def handle(self, *args, **options):
        rows = self._read(options["csv_path"])
        self._check(rows)

        with transaction.atomic():
            created, updated, unchanged = self._apply(rows)
            self.stdout.write(
                f"{len(rows)} subject(s) in the sheet: "
                f"{created} created, {updated} updated, {unchanged} unchanged."
            )
            if not options["write"]:
                self.stdout.write(
                    self.style.WARNING("Dry run: rolled back. Pass --write to keep.")
                )
                transaction.set_rollback(True)
                return

        self.stdout.write(self.style.SUCCESS("Seeded."))
        # Same reason import_subjects says it: this wrote in bulk, and the
        # published files should not wait for the next scheduled run.
        self.stdout.write("Run `manage.py precompute subjects` to publish.")

    def _read(self, csv_path):
        try:
            with open(csv_path, newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            raise CommandError(f"Cannot read {csv_path}: {exc}") from exc
        if not rows:
            raise CommandError(f"{csv_path} has no rows.")
        missing = [column for column in REQUIRED_COLUMNS if column not in rows[0]]
        if missing:
            raise CommandError(
                f"No {', '.join(repr(column) for column in missing)} column. "
                f"Found: {', '.join(rows[0])}"
            )
        return rows

    def _check(self, rows):
        """Everything that has to hold before a single row is written.

        All of it, then one failure naming every problem. A sheet with three
        faults should be fixed once rather than three times, and a partial import
        of a vocabulary is worse than none: the paths that did land become the
        parents the rest are hung from.
        """
        problems = []
        by_slug = {}
        for number, row in enumerate(rows, start=2):
            slug = row[SLUG_COLUMN].strip()
            if not slug:
                problems.append(f"row {number}: no {SLUG_COLUMN}")
                continue
            if slug in by_slug:
                # Globally unique is not a tidiness rule: the detail URL is
                # /subjects/<slug>/ with no path in it, so two subjects sharing a
                # slug means one of them is unreachable.
                problems.append(
                    f"row {number}: {slug} is also on row {by_slug[slug]['_row']}"
                )
                continue
            if not row[NAME_COLUMN].strip():
                problems.append(f"row {number}: {slug} has no {NAME_COLUMN}")
            by_slug[slug] = {**row, "_row": number}

        for slug, row in by_slug.items():
            parent = row[PARENT_COLUMN].strip()
            if parent and parent not in by_slug:
                problems.append(f"row {row['_row']}: {slug} names an unknown parent")
                continue
            expected = self._path(by_slug, slug)
            if expected is None:
                problems.append(f"row {row['_row']}: {slug} is its own ancestor")
                continue
            declared = row[PATH_COLUMN].strip().strip(PATH_SEPARATOR)
            if declared and declared != expected:
                # The sheet carries the path as well as the parent, so the two can
                # disagree. Neither is authoritative over the other here: a
                # disagreement means the sheet is wrong and somebody has to say
                # which half.
                problems.append(
                    f"row {row['_row']}: {slug} says {declared!r} but its parents "
                    f"make {expected!r}"
                )
            depth = expected.count(PATH_SEPARATOR) + 1
            if depth > MAX_DEPTH:
                problems.append(
                    f"row {row['_row']}: {slug} is {depth} levels deep, and "
                    f"{MAX_DEPTH} is the ceiling"
                )

        if problems:
            for problem in problems:
                self.stderr.write(problem)
            raise CommandError(
                f"{len(problems)} problem(s) in the sheet. Nothing was written."
            )

    def _path(self, by_slug, slug):
        """The path a row's parents make, or None if they make a cycle."""
        segments, seen = [], set()
        while slug:
            if slug in seen:
                return None
            seen.add(slug)
            segments.append(slug)
            slug = by_slug[slug][PARENT_COLUMN].strip()
        return PATH_SEPARATOR.join(reversed(segments))

    def _apply(self, rows):
        """Create or update every row, shallowest first.

        Parents before children, so that a child always has one to point at, and
        so that Subject.save derives its path from a parent whose own path is
        already right.

        all_objects, so that a retired subject is found and updated rather than
        created a second time under a slug the database will refuse. Seeding does
        not un-retire it: retiring is a decision made here, and a sheet that still
        lists the subject is not an instruction to reverse it.
        """
        by_slug = {row[SLUG_COLUMN].strip(): row for row in rows}
        paths = {slug: self._path(by_slug, slug) for slug in by_slug}

        def shallowest_first(row):
            path = paths[row[SLUG_COLUMN].strip()]
            return path.count(PATH_SEPARATOR), path

        ordered = sorted(rows, key=shallowest_first)

        existing = {subject.slug: subject for subject in Subject.all_objects.all()}
        created = updated = unchanged = 0
        for row in ordered:
            slug = row[SLUG_COLUMN].strip()
            name = row[NAME_COLUMN].strip()
            description = row[DESCRIPTION_COLUMN].strip()
            parent_slug = row[PARENT_COLUMN].strip()
            parent = existing.get(parent_slug) if parent_slug else None

            subject = existing.get(slug)
            if subject is None:
                existing[slug] = Subject.objects.create(
                    slug=slug, name=name, description=description, parent=parent
                )
                created += 1
                continue

            changes = {}
            if subject.name != name:
                changes["name"] = name
            if subject.description != description:
                changes["description"] = description
            if subject.parent_id != (parent.pk if parent else None):
                changes["parent"] = parent
            if not changes:
                unchanged += 1
                continue
            for field, value in changes.items():
                setattr(subject, field, value)
            # Through save() rather than update(), because moving a subject has to
            # recompute its path and every descendant's, which only save() does.
            subject.save()
            updated += 1
        return created, updated, unchanged
