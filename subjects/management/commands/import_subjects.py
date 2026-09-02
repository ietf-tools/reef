# Copyright The IETF Trust 2026, All Rights Reserved
"""Load a curated sheet of subject assignments, building the tree as it goes.

The source plan.md's open item said was missing. Deriving subjects from keywords
would be a guess dressed as data, and the point of hosting the vocabulary in Reef
was to decide it rather than read it; a curated sheet is a decision, written down.

Two columns matter. `full_paths` carries the hierarchy, one slash-separated path
per subject with a pipe between them, and is what builds the vocabulary: every
path segment becomes a subject, ancestors included, whether or not anything is
filed directly on it. `rfc` names the document each row assigns them to.

Dry run unless --write is given. This is a curation act over the whole back
catalogue, and it should have to be confirmed rather than happen because somebody
pressed up-arrow.
"""

import csv
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from reef.docids import normalize_doc_id
from subjects.models import (
    MAX_DEPTH,
    PATH_SEPARATOR,
    Subject,
    SubjectAssignment,
)

PATH_DELIMITER = "|"


def title_case(slug):
    """A first name for a subject, to be edited by whoever curates it next.

    Deliberately mechanical. The sheet carries slugs rather than names, and a
    guessed expansion of an initialism would be worse than an obvious placeholder:
    "Dkim" reads as something nobody has got to yet, while "Domain Keys" reads as
    a decision.
    """
    return slug.replace("-", " ").title()


class Command(BaseCommand):
    help = "Import subjects and assignments from a curated CSV."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument(
            "--write",
            action="store_true",
            help="Actually write. Without it, nothing is created and the report "
            "is what would have been.",
        )
        parser.add_argument(
            "--path-column",
            default="full_paths",
            help="Column holding pipe-separated slash-separated subject paths.",
        )
        parser.add_argument(
            "--doc-column",
            default="rfc",
            help="Column holding the document identifier.",
        )

    def handle(self, *args, **options):
        rows = self._read(options["csv_path"], options)
        paths, assignments, problems = self._collect(rows)

        # A leaf slug under two different paths breaks the whole arrangement:
        # slugs are globally unique, so one of the two would win silently and the
        # documents filed under the other would land in the wrong branch. A hard
        # failure rather than a warning.
        collisions = self._collisions(paths)
        if collisions:
            for slug, seen in sorted(collisions.items()):
                self.stderr.write(f"{slug} appears under {' and '.join(sorted(seen))}")
            raise CommandError(
                f"{len(collisions)} slug(s) appear under more than one path. "
                "Subject slugs are globally unique, so this has to be settled in "
                "the sheet before it can be imported."
            )

        with transaction.atomic():
            created_subjects = self._create_subjects(sorted(paths))
            created_assignments = self._create_assignments(assignments, problems)
            self._report(paths, assignments, created_subjects, created_assignments)
            for problem in problems:
                self.stderr.write(problem)
            if not options["write"]:
                self.stdout.write(
                    self.style.WARNING("Dry run: rolled back. Pass --write to keep.")
                )
                transaction.set_rollback(True)
                return

        self.stdout.write(self.style.SUCCESS("Imported."))
        # The precomputer's signals fire per row, and this wrote in bulk through
        # paths that do not send them all, so the published files are asked for
        # explicitly rather than left to the daily run.
        self.stdout.write("Run `manage.py precompute subjects` to publish.")

    def _read(self, path, options):
        try:
            with open(path, newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            raise CommandError(f"Cannot read {path}: {exc}") from exc
        if not rows:
            raise CommandError(f"{path} has no rows.")
        for column in (options["path_column"], options["doc_column"]):
            if column not in rows[0]:
                raise CommandError(f"No {column!r} column. Found: {', '.join(rows[0])}")
        self.path_column = options["path_column"]
        self.doc_column = options["doc_column"]
        return rows

    def _collect(self, rows):
        """Every path the sheet names, and every (path, doc) pair it asserts."""
        paths, assignments, problems = set(), [], []
        for number, row in enumerate(rows, start=2):
            raw_doc = (row.get(self.doc_column) or "").strip()
            if not raw_doc:
                continue
            try:
                doc = normalize_doc_id(raw_doc)
            except ValidationError as exc:
                problems.append(f"row {number}: {raw_doc}: {exc.messages[0]}")
                continue
            for path in self._paths(row):
                if path.count(PATH_SEPARATOR) >= MAX_DEPTH:
                    problems.append(
                        f"row {number}: {path} is deeper than {MAX_DEPTH} levels"
                    )
                    continue
                # Every ancestor as well, so that a branch nobody files anything
                # on still exists to hang its children from.
                segments = path.split(PATH_SEPARATOR)
                for index in range(len(segments)):
                    paths.add(PATH_SEPARATOR.join(segments[: index + 1]))
                assignments.append((path, doc))
        return paths, assignments, problems

    def _paths(self, row):
        raw = (row.get(self.path_column) or "").strip()
        if not raw:
            return []
        found = []
        for path in raw.split(PATH_DELIMITER):
            # The sheet spaces the separators for reading: "link-layer / arpanet".
            segments = [segment.strip() for segment in path.split(PATH_SEPARATOR)]
            if all(segments):
                found.append(PATH_SEPARATOR.join(segments))
        return found

    def _collisions(self, paths):
        by_slug = defaultdict(set)
        for path in paths:
            by_slug[path.split(PATH_SEPARATOR)[-1]].add(path)
        return {slug: seen for slug, seen in by_slug.items() if len(seen) > 1}

    def _create_subjects(self, paths):
        """Make every path, shallowest first so a parent exists before its child."""
        existing = {subject.path: subject for subject in Subject.all_objects.all()}
        created = []
        for path in sorted(paths, key=lambda p: (p.count(PATH_SEPARATOR), p)):
            if path in existing:
                continue
            segments = path.split(PATH_SEPARATOR)
            parent = existing.get(PATH_SEPARATOR.join(segments[:-1]))
            subject = Subject.objects.create(
                slug=segments[-1], name=title_case(segments[-1]), parent=parent
            )
            existing[path] = subject
            created.append(subject)
        self._subjects = existing
        return created

    def _create_assignments(self, assignments, problems):
        wanted = {
            (self._subjects[path].pk, doc)
            for path, doc in assignments
            if path in self._subjects
        }
        already = set(SubjectAssignment.objects.values_list("subject_id", "doc"))
        new = sorted(wanted - already)
        SubjectAssignment.objects.bulk_create(
            [
                SubjectAssignment(subject_id=subject_id, doc=doc)
                for subject_id, doc in new
            ]
        )
        return new

    def _report(self, paths, assignments, created_subjects, created_assignments):
        self.stdout.write(f"{len(paths)} subject(s) named by the sheet")
        self.stdout.write(f"  {len(created_subjects)} created")
        containers = [
            subject
            for subject in created_subjects
            if not any(path == subject.path for path, _ in assignments)
        ]
        self.stdout.write(
            f"  {len(containers)} of those carry nothing directly, and exist to "
            "hold what is under them"
        )
        self.stdout.write(f"{len(assignments)} assignment(s) in the sheet")
        self.stdout.write(f"  {len(created_assignments)} created")
