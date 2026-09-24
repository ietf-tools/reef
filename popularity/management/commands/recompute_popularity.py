# Copyright The IETF Trust 2026, All Rights Reserved
from django.core.management.base import BaseCommand

from popularity.compute import recompute_popularity


class Command(BaseCommand):
    help = "Rebuild the popularity table from its ranking sources."

    def handle(self, *args, **options):
        result = recompute_popularity()
        self.stdout.write(
            f"Ranked {result.ranked} document(s), removed {result.deleted}"
        )
