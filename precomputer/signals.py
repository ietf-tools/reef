# Copyright The IETF Trust 2026, All Rights Reserved
"""Re-precompute the curated files when staff change one.

Ratings, subscriptions and set entries are not here on purpose. They arrive
continuously from readers through Red, so a task per write would enqueue thousands
to rebuild a file nobody reads in between; precompute_engagement covers them on a
period instead. What is here is the handful of models a person edits deliberately
and then expects to see published.

Enqueued with a countdown so that saving a subject and its assignments in one sitting
usually lands as one run rather than several, and on_commit so that a rolled-back
admin save does not publish a change that never happened.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from popularity.models import PopularEntry
from subjects.models import Subject, SubjectAssignment
from surveys.models import Survey

logger = logging.getLogger("reef")

# Long enough to absorb a few edits in a row, short enough that staff see their
# change published while they are still looking at it.
CURATED_DEBOUNCE_SECONDS = 60


def _schedule_curated(sender, **kwargs):
    from .tasks import precompute_curated

    def enqueue():
        try:
            precompute_curated.apply_async(countdown=CURATED_DEBOUNCE_SECONDS)
        except Exception:
            # The scheduled runs still cover this; losing the prompt refresh is
            # a smaller harm than a 500 on a save that otherwise succeeded.
            logger.warning(
                "Could not enqueue precompute after %s change",
                sender.__name__,
                exc_info=True,
            )

    transaction.on_commit(enqueue)


for model in (PopularEntry, Subject, SubjectAssignment, Survey):
    receiver(post_save, sender=model, dispatch_uid=f"precompute_{model.__name__}_save")(
        _schedule_curated
    )
    receiver(
        post_delete, sender=model, dispatch_uid=f"precompute_{model.__name__}_delete"
    )(_schedule_curated)
