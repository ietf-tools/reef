# Copyright The IETF Trust 2026, All Rights Reserved
from django.core.management.base import BaseCommand, CommandError

from docsets.models import DocumentSet
from moderation import classify, jev
from moderation.models import ModerationFlag
from moderation.tasks import classify_content
from surveys.models import Response


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
        queued = 0
        for target_type, model in (
            ("documentset", DocumentSet),
            ("response", Response),
        ):
            scored = ModerationFlag.objects.filter(
                target_type=target_type, classifier=jev.CLASSIFIER
            ).values_list("target_id", flat=True)
            pending = model.objects.exclude(pk__in=list(scored)).values_list(
                "pk", flat=True
            )
            for pk in pending[: max(limit - queued, 0)]:
                classify_content.delay(target_type, str(pk))
                queued += 1
        self.stdout.write(f"Queued {queued} item(s) for scoring.")
