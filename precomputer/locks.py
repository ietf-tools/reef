# Copyright The IETF Trust 2026, All Rights Reserved
"""A lock that keeps two precomputer runs from overlapping.

Two runs at once race on the purge: one deletes keys the other has not written yet,
because a key absent from the run doing the purging looks stale rather than in
flight. Runs are also pure waste in parallel, since the second recomputes what the
first just wrote.

A Postgres advisory lock rather than the usual cache.add() one. Reef configures
memcached in production and staging but DummyCache in development, where every
acquisition would appear to succeed and the guard would be a no-op exactly where a
developer would first meet it. The database is the one backend that is the same
everywhere. It also releases on its own if the process dies, which a cache lock only
imitates with a timeout guessed against a run whose length nobody knows yet.
"""

import contextlib
import logging
from zlib import crc32

from django.db import connection

logger = logging.getLogger("reef")


def _key(name):
    """A stable 63-bit int for a lock name, since the lock space is numeric."""
    return crc32(name.encode()) & 0x7FFFFFFF


@contextlib.contextmanager
def advisory_lock(name):
    """Hold a session-level advisory lock, or yield False if somebody else has it.

    Non-blocking on purpose: a scheduled run that finds another in progress should
    say so and stop, not queue up behind it and then do work that has just been
    done.
    """
    key = _key(name)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        acquired = cursor.fetchone()[0]

    if not acquired:
        yield False
        return

    try:
        yield True
    finally:
        with connection.cursor() as cursor:
            # Best effort: if the connection has gone, Postgres has already
            # released the lock with it, which is the property that makes this
            # safe against a worker being killed mid-run.
            try:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [key])
            except Exception:
                logger.warning(
                    "Could not release advisory lock %s", name, exc_info=True
                )
