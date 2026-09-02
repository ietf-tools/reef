# Copyright The IETF Trust 2026, All Rights Reserved
"""Folding one subject into another, taking its documents and its followers with it.

The operation the admin had no mechanism for. Renaming covers a subject whose wording
changed; this covers one whose meaning was absorbed -- "security is now part of
security and privacy" -- where the documents under it belong under the other, and the
people following it were following what it meant rather than what it was called.

Kept out of the model because it is not one write: it moves three kinds of row, decides
what to do about people who already follow both, retires the source, and tells
everybody affected. A model method that did the first four and left the fifth to
whoever remembered is how somebody's subscription changes meaning without being told.
"""

import logging

from django.db import transaction

from .models import SubjectAssignment

logger = logging.getLogger("reef")


class MergeError(Exception):
    """A merge that should not be attempted."""


@transaction.atomic
def merge_subjects(source, target):
    """Move source's children, assignments and followers to target, then retire it.

    Returns the subscription ids that now cover the target on behalf of somebody who
    was following the source, one per affected reader, so the caller can tell them.
    """
    if source.pk == target.pk:
        raise MergeError("A subject cannot be merged into itself.")
    if target.is_retired:
        raise MergeError(
            f"{target} is retired, so merging into it would strand "
            f"{source}'s followers somewhere nobody is offered."
        )
    if source.is_retired:
        raise MergeError(f"{source} is already retired.")
    if target.path in (source.path, *_paths_under(source)):
        raise MergeError(
            f"{target} sits under {source}, so merging one into the other would "
            f"file {source} beneath itself."
        )

    _move_children(source, target)
    _move_assignments(source, target)
    _move_aliases(source, target)
    affected = _move_subscriptions(source, target)

    source.retire(merged_into=target)
    logger.info(
        "Merged subject %s into %s: %s reader(s) affected",
        source.slug,
        target.slug,
        len(affected),
    )
    return affected


def _paths_under(subject):
    from .models import Subject

    return list(Subject.all_objects.under(subject).values_list("path", flat=True))


def _move_children(source, target):
    """Reparent the source's children onto the target.

    Left where they are, they would hang from a retired subject: still offered,
    still taking subscribers, and unreachable in a picker that draws the tree.
    That is the state retire() refuses to create by hand, so a merge must not
    create it either.

    One at a time through save(), not a queryset update, because each child's path
    changes and so does every path beneath it. Bypassing the model here is how the
    denormalisation would go stale on the one operation that moves whole branches.

    The move can breach the depth ceiling where the merge deepens the branch, and
    validate_tree() raises for that inside the transaction, which aborts the merge.
    Refusing beats half-moving.
    """
    for child in list(source.children.all()):
        child.parent = target
        child.save(update_fields=["parent"])


def _move_assignments(source, target):
    """Give the target every document the source had, without duplicating."""
    already = set(target.assignments.values_list("doc", flat=True))
    SubjectAssignment.objects.bulk_create(
        [
            SubjectAssignment(subject=target, doc=doc)
            for doc in source.assignments.values_list("doc", flat=True)
            if doc not in already
        ]
    )
    # The source keeps none: it is retired, and leaving them would have it go on
    # matching changes for documents that are now the target's business.
    source.assignments.all().delete()


def _move_aliases(source, target):
    """Give the target the other names the source answered to.

    Left behind, they would resolve to a subject nothing offers any more, which is
    one hop short of an answer. No deduplicating, unlike the assignments above: an
    alias slug is unique across the table, so the two subjects cannot already share
    a name and there is nothing for the target to win.

    The source's own slug does not become an alias here. It is still a subject's
    slug, so it still resolves at the detail read -- to the retired row, which now
    carries merged_into and says where the name went. An alias of that name would sit
    behind the subject that shadows it and never be reached.
    """
    source.aliases.update(subject=target)


def _move_subscriptions(source, target):
    """Repoint the source's followers at the target.

    A reader already following both is the case that needs deciding, because
    unique(user, kind, params, document_set, subject) will not hold two identical
    subscriptions: their source subscription is deleted rather than repointed, and
    they are told once through the one they keep. Repointing blindly would raise, and
    dropping them from the affected list would leave somebody whose subscription
    quietly changed meaning with no word about it.
    """
    following_target = dict(target.subscriptions.values_list("user_id", "pk"))
    affected = []
    for subscription in source.subscriptions.select_related("user"):
        existing = following_target.get(subscription.user_id)
        if existing is not None:
            subscription.delete()
            affected.append(existing)
            continue
        subscription.subject = target
        subscription.save(update_fields=["subject"])
        following_target[subscription.user_id] = subscription.pk
        affected.append(subscription.pk)
    return affected


def merge_message(source, target):
    """The line a reader is told when a subject they follow has been merged."""
    return (
        f"The subject {source.name}, which you asked to be notified about, is now "
        f"part of {target.name}. Your notifications continue, and now cover the "
        f"documents under {target.name}."
    )


def notify_merge(source, target, subscription_ids):
    """Tell everybody whose subscription now means something else.

    Through the ordinary notification queue rather than a message of its own, so it
    inherits everything that path already settled: written to the database before it
    is enqueued, one mail per reader, and held rather than sent if the deployment has
    no unsubscribe URL. The event carries no document, which the digest template and
    subject line already handle, because this is news about the vocabulary rather
    than about an RFC.
    """
    from subscriptions.models import Subscription
    from subscriptions.tasks import queue_notification

    change = merge_message(source, target)
    for subscription in Subscription.objects.filter(pk__in=subscription_ids):
        queue_notification(
            subscription.user_id,
            [subscription.pk],
            [{"doc": "", "change": change, "url": ""}],
        )


def merge_and_notify(source, target):
    """The whole operation, which is the only way it should be performed."""
    affected = merge_subjects(source, target)
    notify_merge(source, target, affected)
    return affected
