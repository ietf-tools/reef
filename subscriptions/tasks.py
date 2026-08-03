# Copyright The IETF Trust 2026, All Rights Reserved
"""Async notification delivery (scaffold).

The full build wires ingestion of RFC-change events from the datatracker
(ticket #139) to matching subscriptions and sends email. For now these are
stubs that establish the interface and the celery task boundary.
"""

import logging

from celery import shared_task

logger = logging.getLogger("reef")


@shared_task(ignore_result=True)
def send_subscription_email(subscription_id: int, event: dict) -> None:
    """Send one notification email for a matched subscription (stub)."""
    logger.info(
        "send_subscription_email (stub): subscription=%s event=%s",
        subscription_id,
        event,
    )


def ingest_rfc_change(event: dict) -> None:
    """Entry point for datatracker RFC-change events (stub).

    The full implementation finds subscriptions matching the event and enqueues
    send_subscription_email for each. Wired to a datatracker feed later.
    """
    logger.info("ingest_rfc_change (stub): event=%s", event)
