# Copyright The IETF Trust 2026, All Rights Reserved
import logging
import urllib.error

from celery import shared_task
from django.conf import settings

from reef.tasks import RetryTask

from . import classify

logger = logging.getLogger("reef")

# Jev's own words for "try again": rate limited, and overloaded. A server error is
# retried too. Anything else, a bad key or a request it will not accept, would fail
# the same way every time.
RETRY_STATUSES = frozenset({429, 529})


@shared_task(
    base=RetryTask,
    bind=True,
    # Every 15 minutes for a day at the tail rate: a score that arrives tomorrow is
    # still useful, and one that never does is only a gap the backfill can fill.
    max_retries=4 * 24,
    ignore_result=True,
)
def classify_content(self, target_type, target_id):
    """Score one set or response. Never raises for the content itself.

    A failure leaves no flag, which is how "unchecked" is recorded: it is never
    written as a clean score, and the classify_unscored command finds the gap.
    Nothing from the content is logged, nor anything Jev sent back.
    """
    if not settings.REEF_JEV_API_KEY:
        return
    try:
        classify.score(target_type, target_id)
    except urllib.error.HTTPError as exc:
        if exc.code in RETRY_STATUSES or exc.code >= 500:
            raise self.retry(exc=exc) from exc
        logger.error(
            "Jev refused to score %s %s: HTTP %s", target_type, target_id, exc.code
        )
    except (urllib.error.URLError, TimeoutError) as exc:
        raise self.retry(exc=exc) from exc
    except ValueError:
        logger.error(
            "Jev gave an answer that could not be read for %s %s",
            target_type,
            target_id,
        )
