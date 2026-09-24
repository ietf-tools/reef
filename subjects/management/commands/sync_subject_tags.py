# Copyright The IETF Trust 2026, All Rights Reserved
"""Mirror the subject vocabulary and assignments from rfc-editor/rfc-subject-tags.

The shell-side entry point to subjects.sync.run_sync -- see that module for the
design. Dry run unless --write is given, like every other importer in this app.
Manual only: nothing schedules this.
"""

from django.core.management.base import BaseCommand

from subjects.sync import RFC_TAGS_URL, TAXONOMY_URL, run_sync


class Command(BaseCommand):
    help = (
        "Sync the subject vocabulary and assignments from rfc-editor/rfc-subject-tags."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--write",
            action="store_true",
            help="Actually write. Without it, nothing is kept and the report is "
            "what would have happened.",
        )
        parser.add_argument(
            "--vocabulary-url",
            default=TAXONOMY_URL,
            help="Where to fetch taxonomy.yaml from.",
        )
        parser.add_argument(
            "--assignments-url",
            default=RFC_TAGS_URL,
            help="Where to fetch rfc-tags.json from.",
        )
        parser.add_argument(
            "--confirm-large-change",
            action="store_true",
            help="Proceed even if this run would retire an unusually large share "
            "of the vocabulary or drop total assignments by half or more.",
        )

    def handle(self, *args, **options):
        result = run_sync(
            vocabulary_url=options["vocabulary_url"],
            assignments_url=options["assignments_url"],
            confirm_large_change=options["confirm_large_change"],
            write=options["write"],
        )

        if result.skipped:
            raise SystemExit(self.style.WARNING(result.skip_reason))
        if result.validation_problems:
            for problem in result.validation_problems:
                self.stderr.write(problem)
            raise SystemExit(
                self.style.ERROR(
                    f"{len(result.validation_problems)} problem(s) in the fetched "
                    "taxonomy or against the current vocabulary. Nothing was "
                    "written."
                )
            )
        if result.needs_confirmation:
            self.stdout.write(
                self.style.WARNING(
                    f"Would retire {result.retire_count} of "
                    f"{result.live_count_before} live subject(s), and change "
                    f"assignments from {result.assignment_total_before} to "
                    f"{result.assignment_total_after}. Pass --confirm-large-change "
                    "to apply this anyway. Nothing was written."
                )
            )
            return

        self.stdout.write(
            f"{len(result.created)} created, {len(result.updated)} updated, "
            f"{len(result.unretired)} unretired, {len(result.retired)} retired"
        )
        self.stdout.write(
            f"{result.assignments_created} assignment(s) created, "
            f"{result.assignments_deleted} deleted"
        )
        for slug, doc in result.unresolved_assignments:
            self.stderr.write(f"{slug}: no such subject or document (doc {doc})")
        for slug, suggestions in result.suggestions.items():
            if not suggestions:
                self.stdout.write(f"{slug}: retired, no likely successor found")
                continue
            for suggestion in suggestions:
                self.stdout.write(
                    f"{slug}: retired, possible match {suggestion.target_slug} "
                    f"({suggestion.shared_count}/{suggestion.old_count} "
                    "documents shared)"
                )

        if not options["write"]:
            self.stdout.write(
                self.style.WARNING("Dry run: rolled back. Pass --write to keep.")
            )
            return
        self.stdout.write(self.style.SUCCESS("Synced."))
        self.stdout.write("Run `manage.py precompute subjects` to publish.")
