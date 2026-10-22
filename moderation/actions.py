# Copyright The IETF Trust 2026, All Rights Reserved
"""What a moderator can do, each recorded in the moderator log.

Every function here changes state and writes its log rows in one transaction, so
an action is never done without a record or recorded without being done. The admin
calls these rather than editing rows, and a task or command that moderates would too.

Soft delete only. Hard deletion of spam, after a retention period, is separate work.
"""

from django.db import transaction
from django.utils import timezone

from docsets.models import DocumentSet
from surveys.models import Response

from .models import ModerationAction

Action = ModerationAction.Action

# What an account block writes as the reason on the content it removes, so that
# the content's own admin page says why it is gone.
BLOCK_CONSEQUENCE_REASON = "Account blocked."


class CannotBlock(Exception):
    """The account may not be blocked; the message says why, for the moderator."""


def _name(user):
    return (user.get_username() if user else "")[:255]


def log_action(
    actor,
    action,
    target_type,
    target_id,
    *,
    subject_user=None,
    reason="",
    snapshot=None,
    caused_by=None,
):
    return ModerationAction.objects.create(
        actor=actor,
        actor_name=_name(actor),
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        subject_user=subject_user,
        subject_sub=(subject_user.oidc_sub or "") if subject_user else "",
        subject_name=_name(subject_user),
        reason=reason,
        snapshot=snapshot or {},
        caused_by=caused_by,
    )


def snapshot_set(document_set):
    return {
        "title": document_set.title,
        "description": document_set.description,
        "documents": list(document_set.entries.values_list("doc", flat=True)),
    }


def snapshot_survey(survey):
    return {
        "slug": survey.slug,
        "title": survey.title,
        "description": survey.description,
        "status": survey.status,
        "visibility": survey.visibility,
    }


def snapshot_response(response):
    return {
        "survey": response.survey.slug,
        "data": response.data,
        "submitted_at": response.submitted_at.isoformat(),
        "duration_seconds": response.duration_seconds,
        "pages_viewed": response.pages_viewed,
    }


@transaction.atomic
def take_down_set(actor, document_set, reason="", *, caused_by=None):
    """Returns the log row, or None if the set was already down."""
    if document_set.is_deleted:
        return None
    entry = log_action(
        actor,
        Action.TAKE_DOWN_SET,
        "documentset",
        document_set.pk,
        subject_user=document_set.owner,
        reason=reason,
        snapshot=snapshot_set(document_set),
        caused_by=caused_by,
    )
    document_set.soft_delete(reason)
    return entry


@transaction.atomic
def restore_set(actor, document_set, reason="", *, caused_by=None):
    if not document_set.is_deleted:
        return None
    entry = log_action(
        actor,
        Action.RESTORE_SET,
        "documentset",
        document_set.pk,
        subject_user=document_set.owner,
        reason=reason,
        snapshot=snapshot_set(document_set),
        caused_by=caused_by,
    )
    document_set.restore()
    return entry


@transaction.atomic
def withdraw_survey(actor, survey, reason=""):
    if survey.is_deleted:
        return None
    entry = log_action(
        actor,
        Action.WITHDRAW_SURVEY,
        "survey",
        survey.pk,
        subject_user=survey.created_by,
        reason=reason,
        snapshot=snapshot_survey(survey),
    )
    survey.soft_delete(reason)
    return entry


@transaction.atomic
def restore_survey(actor, survey, reason=""):
    if not survey.is_deleted:
        return None
    entry = log_action(
        actor,
        Action.RESTORE_SURVEY,
        "survey",
        survey.pk,
        subject_user=survey.created_by,
        reason=reason,
        snapshot=snapshot_survey(survey),
    )
    survey.restore()
    return entry


@transaction.atomic
def delete_responses(actor, responses, reason="", *, caused_by=None):
    """Remove the live responses among `responses`, one log row each."""
    removed = []
    live = responses.filter(deleted_at__isnull=True).select_related(
        "survey", "submitted_by"
    )
    for response in live:
        removed.append(
            log_action(
                actor,
                Action.DELETE_RESPONSE,
                "response",
                response.pk,
                subject_user=response.submitted_by,
                reason=reason,
                snapshot=snapshot_response(response),
                caused_by=caused_by,
            )
        )
    Response.all_objects.filter(pk__in=[int(row.target_id) for row in removed]).update(
        deleted_at=timezone.now(), deleted_reason=reason
    )
    return removed


@transaction.atomic
def restore_responses(actor, responses, reason="", *, caused_by=None):
    restored = []
    deleted = responses.filter(deleted_at__isnull=False).select_related(
        "survey", "submitted_by"
    )
    for response in deleted:
        restored.append(
            log_action(
                actor,
                Action.RESTORE_RESPONSE,
                "response",
                response.pk,
                subject_user=response.submitted_by,
                reason=reason,
                snapshot=snapshot_response(response),
                caused_by=caused_by,
            )
        )
    Response.all_objects.filter(pk__in=[int(row.target_id) for row in restored]).update(
        deleted_at=None, deleted_reason=""
    )
    return restored


@transaction.atomic
def block_user(actor, user, reason="", *, remove_content=False):
    """Refuse the account's API access, optionally taking its content down too.

    Staff accounts are refused, and so is blocking yourself: a block locks the
    account out, and the one tier of admin there is has no way back in. An account
    that is already blocked is left alone and returns None.
    """
    if user.is_staff:
        raise CannotBlock("Staff accounts cannot be blocked.")
    if actor is not None and actor.pk == user.pk:
        raise CannotBlock("You cannot block your own account.")
    if user.is_blocked:
        return None

    user.blocked_at = timezone.now()
    user.blocked_reason = reason
    user.blocked_by = actor
    user.save(update_fields=["blocked_at", "blocked_reason", "blocked_by"])
    block = log_action(
        actor,
        Action.BLOCK_USER,
        "user",
        user.pk,
        subject_user=user,
        reason=reason,
        snapshot={"removed_content": remove_content},
    )
    if remove_content:
        for document_set in DocumentSet.objects.filter(owner=user):
            take_down_set(
                actor, document_set, BLOCK_CONSEQUENCE_REASON, caused_by=block
            )
        delete_responses(
            actor,
            Response.objects.filter(submitted_by=user),
            BLOCK_CONSEQUENCE_REASON,
            caused_by=block,
        )
    return block


@transaction.atomic
def unblock_user(actor, user, reason=""):
    """Lift the local block and put back what that block took down.

    Content a moderator removed separately stays removed. Authentik is never
    touched: an account Reef did not itself disable upstream is not Reef's to
    reactivate, and one it did is reactivated there, by hand.
    """
    if not user.is_blocked:
        return None
    block = (
        ModerationAction.objects.filter(action=Action.BLOCK_USER, subject_user=user)
        .order_by("-created_at", "-id")
        .first()
    )
    user.blocked_at = None
    user.blocked_reason = ""
    user.blocked_by = None
    user.save(update_fields=["blocked_at", "blocked_reason", "blocked_by"])
    entry = log_action(
        actor,
        Action.UNBLOCK_USER,
        "user",
        user.pk,
        subject_user=user,
        reason=reason,
        caused_by=block,
    )
    if block is not None:
        consequences = block.consequences.all()
        set_ids = consequences.filter(action=Action.TAKE_DOWN_SET).values_list(
            "target_id", flat=True
        )
        for document_set in DocumentSet.all_objects.filter(pk__in=list(set_ids)):
            restore_set(actor, document_set, reason, caused_by=entry)
        response_ids = [
            int(target_id)
            for target_id in consequences.filter(
                action=Action.DELETE_RESPONSE
            ).values_list("target_id", flat=True)
        ]
        restore_responses(
            actor,
            Response.all_objects.filter(pk__in=response_ids),
            reason,
            caused_by=entry,
        )
    return entry
