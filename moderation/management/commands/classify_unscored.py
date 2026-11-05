# Copyright The IETF Trust 2026, All Rights Reserved
from django.core.management.base import BaseCommand, CommandError

from moderation import backfill, classify


class Command(BaseCommand):
    help = (
        "Queue every live set and response that has no spam score yet. Run it to "
        "score existing content in shadow mode, and to fill gaps left by a failure."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            required=True,
            help="The most items to queue, so that a first run is a trial.",
        )

    def handle(self, *args, limit, **options):
        if not classify.enabled():
            raise CommandError("REEF_JEV_API_KEY is not set, so nothing can be scored.")
        queued, _ = backfill.queue_unscored(limit)
        self.stdout.write(f"Queued {queued} item(s) for scoring.")
