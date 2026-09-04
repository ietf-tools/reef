# Copyright The IETF Trust 2026, All Rights Reserved
"""Assign documents to subjects that already exist, from a curated sheet.

The third of the three, and the one that fills the counts in. seed_subjects
loads the vocabulary; this files documents under it; import_subjects is the
older route that did both at once, building subjects out of the paths in an
assignment sheet.

That older route is the wrong tool once a vocabulary has been seeded, and the
reason is worth writing down. An assignment sheet carries a path per tag as well
as the tag itself, and the two sheets can disagree about where a subject sits
without disagreeing about which subjects exist -- the assignment sheet in hand
files `arpanet` under `link-layer` where the vocabulary makes it a root, and 150
of its 488 paths differ that way. Building subjects from those paths would try
to create a second `arpanet`, which the unique slug refuses, and where it did
not refuse it would file documents under a branch nobody curated.

So this resolves by leaf slug and creates no subjects at all. That works because
slugs are globally unique, which is the property the whole flat-tag arrangement
rests on: a tag names exactly one subject, wherever the vocabulary has since
decided to put it. The sheet's own path column is not read.

A tag naming no subject stops the run. Documents would otherwise lose a subject
somebody wrote down, silently and in bulk, and the fix is a decision about the
vocabulary rather than something this should guess at. --skip-unknown says to
proceed anyway and reports what was dropped.

Dry run unless --write is given, as its two companions are.
"""

import csv
from collections import defaultdict
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from reef.docids import normalize_doc_id
from subjects.models import Subject, SubjectAssignment

TAG_SEPARATOR = ";"
DEFAULT_SHEET = Path(__file__).resolve().parents[2] / "seed" / "rfc-tag-assignments.csv"


class Command(BaseCommand):
    help = "Assign documents to existing subjects from a curated CSV."

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
        parser.add_argument(
            "--doc-column",
            default="rfc",
            help="Column holding the document identifier.",
        )
        parser.add_argument(
            "--tag-column",
            default="tags",
            help=f"Column holding {TAG_SEPARATOR!r}-separated subject slugs.",
        )
        parser.add_argument(
            "--skip-unknown",
            action="store_true",
            help="Drop assignments naming a subject the vocabulary does not have, "
            "rather than refusing the sheet.",
        )

    def handle(self, *args, **options):
        rows = self._read(options)
        pairs, unknown, problems = self._collect(rows, options)

        for problem in problems:
            self.stderr.write(problem)
        if unknown:
            for slug, count in sorted(unknown.items(), key=lambda item: -item[1]):
                self.stderr.write(f"{slug}: no such subject, {count} assignment(s)")
            if not options["skip_unknown"]:
                raise CommandError(
                    f"{len(unknown)} tag(s) name no subject. Add them to the "
                    "vocabulary sheet and seed again, or pass --skip-unknown to "
                    "import the rest without them."
                )

        with transaction.atomic():
            created, existing = self._assign(pairs)
            self.stdout.write(
                f"{len(rows)} row(s), {len(pairs)} assignment(s): "
                f"{created} created, {existing} already there."
            )
            if not options["write"]:
                self.stdout.write(
                    self.style.WARNING("Dry run: rolled back. Pass --write to keep.")
                )
                transaction.set_rollback(True)
                return

        self.stdout.write(self.style.SUCCESS("Imported."))
        self.stdout.write("Run `manage.py precompute subjects` to publish.")

    def _read(self, options):
        try:
            with open(options["csv_path"], newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
        except OSError as exc:
            raise CommandError(f"Cannot read {options['csv_path']}: {exc}") from exc
        if not rows:
            raise CommandError(f"{options['csv_path']} has no rows.")
        for column in (options["doc_column"], options["tag_column"]):
            if column not in rows[0]:
                raise CommandError(f"No {column!r} column. Found: {', '.join(rows[0])}")
        return rows

    def _collect(self, rows, options):
        """Every (subject, document) pair the sheet asserts, and what it got wrong.

        The whole sheet is read before anything is written, so that an unknown tag
        can stop the run rather than stopping it a third of the way through.
        """
        known = {
            subject.slug: subject.pk for subject in Subject.objects.only("pk", "slug")
        }
        pairs, unknown, problems = set(), defaultdict(int), []
        for number, row in enumerate(rows, start=2):
            raw_doc = (row.get(options["doc_column"]) or "").strip()
            if not raw_doc:
                continue
            try:
                doc = normalize_doc_id(raw_doc)
            except ValidationError as exc:
                problems.append(f"row {number}: {raw_doc}: {exc.messages[0]}")
                continue
            tags = (row.get(options["tag_column"]) or "").split(TAG_SEPARATOR)
            for tag in (tag.strip() for tag in tags):
                if not tag:
                    continue
                subject_id = known.get(tag)
                if subject_id is None:
                    unknown[tag] += 1
                    continue
                # A set, because a sheet naming the same subject twice on one row
                # is asserting one thing twice rather than two things.
                pairs.add((subject_id, doc))
        return pairs, unknown, problems

    def _assign(self, pairs):
        """Create what is missing, in one insert, and leave what is there alone.

        bulk_create with ignore_conflicts rather than get_or_create per pair:
        nineteen thousand round trips is a different command from one, and the
        uniqueness constraint is what decides a duplicate either way.
        """
        before = SubjectAssignment.objects.count()
        SubjectAssignment.objects.bulk_create(
            [
                SubjectAssignment(subject_id=subject_id, doc=doc)
                for subject_id, doc in sorted(pairs)
            ],
            ignore_conflicts=True,
            batch_size=2000,
        )
        created = SubjectAssignment.objects.count() - before
        return created, len(pairs) - created
