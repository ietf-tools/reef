# Copyright The IETF Trust 2026, All Rights Reserved
"""Stage a digest line for the readers who asked to hear when a document is
newly given a subject.

Two things tag a document: staff in the admin, one assignment at a time, and
the sync from rfc-editor/rfc-subject-tags, thousands at once. Both are the same
news to a subscriber, so both come through here, with the matching and the
staging done once per batch rather than once per row.

Matching is on the subject and its ancestors, not on the document: a reader
following HTTP learns nothing from the same document also becoming Security.
Retired ancestors still count, because their subscribers go on matching
(Subscription.subject is PROTECT for exactly that reason).
"""

import logging
from collections import defaultdict

from django.conf import settings
from django.db import IntegrityError, transaction

from subjects.models import Subject, ancestor_paths

from .models import Subscription

logger = logging.getLogger("reef")

EVENT_KIND = "subject_assignment"


def assignment_event_key(subject_id, doc):
    """Keyed on the fact, not the row, so a tag removed and re-added before the
    digest, or applied by staff and then again by the sync, is one line."""
    return f"subject-assignment:{subject_id}:{doc}"


def _document_url(doc):
    """Red's canonical page for the document, matching changes.DocumentChange.url."""
    base = settings.REEF_RFC_SITE_URL.rstrip("/")
    return f"{base}/info/{doc}/"


def stage_assignment_events(pairs):
    """Stage one line per reader for every (subject_id, doc) newly assigned.

    Returns how many rows were staged. Runs in the caller's transaction: each
    stage is its own savepoint, so a fact already staged for a reader is skipped
    by the uniqueness constraint without poisoning the enclosing transaction.
    """
    from .tasks import stage_subject_event

    pairs = list(pairs)
    subject_ids = {subject_id for subject_id, _ in pairs}
    if not subject_ids:
        return 0

    subjects = {
        pk: (name, path)
        for pk, name, path in Subject.all_objects.filter(
            pk__in=subject_ids
        ).values_list("pk", "name", "path")
    }
    paths_above = {
        path
        for _, subject_path in subjects.values()
        for path in ancestor_paths(subject_path)
    }
    pk_by_path = dict(
        Subject.all_objects.filter(path__in=paths_above).values_list("path", "pk")
    )
    covering = {
        pk: [
            pk,
            *(
                pk_by_path[path]
                for path in ancestor_paths(subject_path)
                if path in pk_by_path
            ),
        ]
        for pk, (_, subject_path) in subjects.items()
    }

    # (user_id, subscription pk) per covering subject, loaded once for the batch.
    subscriptions_by_subject = defaultdict(list)
    covering_ids = {pk for pks in covering.values() for pk in pks}
    for user_id, pk, subject_id in Subscription.objects.filter(
        kind=Subscription.Kind.SUBJECT, subject_id__in=covering_ids
    ).values_list("user_id", "pk", "subject_id"):
        subscriptions_by_subject[subject_id].append((user_id, pk))

    staged = 0
    for subject_id, doc in pairs:
        if subject_id not in subjects:
            continue
        # One row per reader: following both Email and Messaging is one line with
        # two reasons, and a second row would break the (user, kind, key) constraint.
        subscription_ids_by_user = defaultdict(list)
        for covering_id in covering[subject_id]:
            for user_id, pk in subscriptions_by_subject.get(covering_id, ()):
                subscription_ids_by_user[user_id].append(pk)
        if not subscription_ids_by_user:
            continue

        name, _ = subjects[subject_id]
        event = {
            "doc": doc,
            "change": f"Added to the subject {name}.",
            "url": _document_url(doc),
        }
        event_key = assignment_event_key(subject_id, doc)
        for user_id, subscription_ids in subscription_ids_by_user.items():
            try:
                with transaction.atomic():
                    stage_subject_event(
                        user_id, subscription_ids, EVENT_KIND, event_key, event
                    )
            except IntegrityError:
                logger.info(
                    "Assignment of %s to subject %s already staged for user %s",
                    doc,
                    subject_id,
                    user_id,
                )
                continue
            staged += 1
    return staged
