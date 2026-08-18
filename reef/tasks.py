# Copyright The IETF Trust 2026, All Rights Reserved
"""Celery task base classes shared across the Reef apps.

Follows Purple's utils/task_utils.py. The reason a notification task wants
this rather than Celery's defaults: a transient SMTP failure should not be the
end of a subscriber's notification, and a permanent one should not be silent.
"""

import logging
from email.utils import formataddr
from textwrap import dedent

from celery import Task
from django.conf import settings

from reef.mail import send_mail

logger = logging.getLogger("reef")


class RetryTask(Task):
    """A task that retries on a backoff schedule and reports giving up.

    acks_late means a task whose worker dies is redelivered rather than lost:
    for notification mail a duplicate is a smaller harm than a silent drop.
    """

    max_retries = 4 * 24 * 7  # every 15 minutes for a week, at the tail rate
    acks_late = True

    # Seconds between attempts. Front-loaded so that a broker or SMTP restart
    # is ridden out in seconds, then flattening to 15 minutes so that a longer
    # outage does not turn into a retry storm.
    retry_delay_schedule = [3, 3, 6, 10, 15, 30, 60, 120, 240, 480, 900]

    def _retry_delay(self, n):
        if n < len(self.retry_delay_schedule):
            return self.retry_delay_schedule[n]
        return self.retry_delay_schedule[-1]

    def retry(
        self,
        args=None,
        kwargs=None,
        exc=None,
        throw=True,
        eta=None,
        countdown=None,
        max_retries=None,
        **options,
    ):
        if countdown is None:
            countdown = self._retry_delay(self.request.retries)
        super().retry(args, kwargs, exc, throw, eta, countdown, max_retries, **options)

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(
            "Task failed: %s[%s] args=%s kwargs=%s. Giving up after %s retries.",
            self.name,
            task_id,
            args,
            kwargs,
            self.request.retries,
        )
        if not settings.ADMINS:
            # ADMINS is optional in every environment, so the log above is the
            # only report a deployment without it gets.
            return
        # If the failure was itself a mail failure this will very likely fail
        # too, which is why the log comes first.
        send_mail(
            to=[formataddr(admin) for admin in settings.ADMINS],
            subject=f"Reef task failed: {self.name}[{task_id}]",
            msg=dedent(f"""\
                Reef task {self.name} failed.

                Giving up after {self.request.retries} attempts.

                Task name: {self.name}
                Task id: {task_id}
                Task args: {args}
                Task kwargs: {kwargs}
                Exception: {exc!r}

            """)
            + str(einfo),
            fail_silently=True,  # nothing useful is left to do with a raise here
        )
