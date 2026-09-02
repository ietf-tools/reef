# Copyright The IETF Trust 2026, All Rights Reserved
"""Recompute every subject's derived path and depth from its parent.

The path column is a denormalisation, and a denormalisation needs a way back to
the truth. Subject.save() keeps it honest for every write that goes through the
model, which is every write the admin and the API make; what this covers is the
rest -- a queryset update that named the parent, a restore from a dump taken
mid-migration, a bug since fixed -- where the column and the pointers it derives
from have been allowed to disagree.

Safe to run at any time: it reads the parents, writes the paths, and changes
nothing when they already agree. --check reports without writing, which is what
a deployment or a test wants.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from subjects.models import PATH_SEPARATOR, Subject


class Command(BaseCommand):
    help = "Recompute subject path and depth from parent pointers."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Report the subjects whose path is wrong and exit without writing.",
        )

    def handle(self, *args, **options):
        # Ordered by depth so that a parent's path is already correct by the time
        # its children are reached, which is what lets one pass do the whole tree.
        subjects = list(Subject.all_objects.order_by("depth", "path"))
        by_pk = {subject.pk: subject for subject in subjects}

        wrong = []
        for subject in subjects:
            parent = by_pk.get(subject.parent_id)
            path = (
                subject.slug
                if parent is None
                else f"{parent.path}{PATH_SEPARATOR}{subject.slug}"
            )
            depth = path.count(PATH_SEPARATOR)
            if (path, depth) != (subject.path, subject.depth):
                wrong.append((subject, subject.path, path))
                subject.path = path
                subject.depth = depth

        for subject, was, now in wrong:
            self.stdout.write(f"{subject.slug}: {was or '(blank)'} -> {now}")

        if options["check"]:
            self.stdout.write(
                self.style.SUCCESS("Every path is derived correctly.")
                if not wrong
                else self.style.ERROR(f"{len(wrong)} subject(s) have a stale path.")
            )
            return

        if wrong:
            with transaction.atomic():
                Subject.all_objects.bulk_update(
                    [subject for subject, _, _ in wrong], ["path", "depth"]
                )
        self.stdout.write(
            self.style.SUCCESS(f"Rebuilt {len(wrong)} of {len(subjects)} path(s).")
        )
