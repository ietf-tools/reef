# Copyright The IETF Trust 2026, All Rights Reserved
"""What a moderator can do, each recorded in the moderator log.

Every function here changes state and writes its log rows in one transaction, so
an action is never done without a record or recorded without being done. The admin
calls these rather than editing rows, and a task or command that moderates would too.

Soft delete only. Hard deletion of spam, after a retention period, is separate work.
"""

import urllib.error

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from docsets.models import DocumentSet
from ratings.models import Rating
from reefauth import deactivation
from surveys.models import Response

from .models import ModerationAction

Action = ModerationAction.Action

# The account service logs a reason with every deactivation and a blocked account
# always has one, so a block with nothing written gets this.
DEFAULT_REASON = "Spam"

# How much of the account service's answer is kept in the log: enough to read why it
# said no, not enough to hold anything else.
DETAIL_LENGTH = 300

# What an account block writes as the reason on the content it removes, so that
# the content's own admin page says why it is gone.
BLOCK_CONSEQUENCE_REASON = "Account blocked."


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


def snapshot_rating(rating):
    return {"rfc": rating.rfc, "value": rating.value}


def _set_rating_removed(rating, reason, removed):
    # Saved one at a time, not updated in bulk, so that the save signal marks the
    # document and its statistics are refreshed.
    rating.deleted_at = timezone.now() if removed else None
    rating.deleted_reason = reason
    rating.save(update_fields=["deleted_at", "deleted_reason", "updated_at"])


@transaction.atomic
def delete_ratings(actor, ratings, reason="", *, caused_by=None):
    """Remove the live ratings among `ratings`, one log row each."""
    removed = []
    for rating in ratings.filter(deleted_at__isnull=True).select_related("user"):
        removed.append(
            log_action(
                actor,
                Action.DELETE_RATING,
                "rating",
                rating.pk,
                subject_user=rating.user,
                reason=reason,
                snapshot=snapshot_rating(rating),
                caused_by=caused_by,
            )
        )
        _set_rating_removed(rating, reason, True)
    return removed


@transaction.atomic
def restore_ratings(actor, ratings, reason="", *, caused_by=None):
    restored = []
    for rating in ratings.filter(deleted_at__isnull=False).select_related("user"):
        restored.append(
            log_action(
                actor,
                Action.RESTORE_RATING,
                "rating",
                rating.pk,
                subject_user=rating.user,
                reason=reason,
                snapshot=snapshot_rating(rating),
                caused_by=caused_by,
            )
        )
        _set_rating_removed(rating, "", False)
    return restored


def validate_blockable(actor, user):
    """Raise ValidationError if actor may not block user."""
    if actor is not None and actor.pk == user.pk:
        raise ValidationError("You cannot block your own account.")


@transaction.atomic
def block_user(actor, user, reason="", *, remove_content=False):
    """Refuse the account's API access, optionally taking its content down too.

    Blocking yourself is refused, since a block would lock you out of the moderation
    page that undoes it. Which other accounts must never be deactivated is the account
    API's rule, not this function's. An account that is already blocked is left alone
    and returns None.
    """
    validate_blockable(actor, user)
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
        delete_ratings(
            actor,
            Rating.objects.filter(user=user),
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
        rating_ids = [
            int(target_id)
            for target_id in consequences.filter(
                action=Action.DELETE_RATING
            ).values_list("target_id", flat=True)
        ]
        restore_ratings(
            actor,
            Rating.all_objects.filter(pk__in=rating_ids),
            reason,
            caused_by=entry,
        )
    return entry


class Deactivation:
    """How a request to the account service went. `status` is one of the
    constants below, and is what the log row records."""

    DEACTIVATED = "deactivated"
    REFUSED = "refused"
    UNREACHABLE = "unreachable"
    NO_ACCOUNT_UUID = "no_account_uuid"

    def __init__(self, status, http_status=None, detail=""):
        self.status = status
        self.http_status = http_status
        self.detail = detail


def deactivation_enabled():
    return bool(
        settings.REEF_ADMIN_SERVICE_OIDC_CLIENT_ID
        and settings.REEF_ADMIN_SERVICE_OIDC_CLIENT_SECRET
        and settings.REEF_ACCOUNT_DEACTIVATE_USER_URL
    )


def _call_account_service(account_uuid, reason):
    try:
        token = deactivation.fetch_token(
            settings.OIDC_OP_TOKEN_ENDPOINT,
            settings.REEF_ADMIN_SERVICE_OIDC_CLIENT_ID,
            settings.REEF_ADMIN_SERVICE_OIDC_CLIENT_SECRET,
        )
        status = deactivation.deactivate(
            settings.REEF_ACCOUNT_DEACTIVATE_USER_URL, token, account_uuid, reason
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read(DETAIL_LENGTH).decode(errors="replace")
        return Deactivation(Deactivation.REFUSED, exc.code, detail)
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return Deactivation(Deactivation.UNREACHABLE, detail=type(exc).__name__)
    return Deactivation(Deactivation.DEACTIVATED, status)


def deactivate_account(actor, user, reason, *, caused_by=None):
    """Ask the account service to deactivate user, now, and log how it went.

    Never inside a transaction: the service has acted by the time it answers, so a
    rollback afterwards would erase the record of something that happened. Returns
    the Deactivation, which the caller shows the moderator.

    Reef never asks for a reactivation. An account the service holds disabled is
    enabled again there, by whoever administers accounts.
    """
    reason = reason.strip() or DEFAULT_REASON
    if user.account_uuid is None:
        outcome = Deactivation(Deactivation.NO_ACCOUNT_UUID)
    else:
        outcome = _call_account_service(user.account_uuid, reason)
    log_action(
        actor,
        Action.DEACTIVATE_ACCOUNT,
        "user",
        user.pk,
        subject_user=user,
        reason=reason,
        snapshot={
            "status": outcome.status,
            "http_status": outcome.http_status,
            "detail": outcome.detail,
        },
        caused_by=caused_by,
    )
    return outcome


def block_and_deactivate(actor, user, reason, *, remove_content=False):
    """Block locally, then ask the account service to deactivate.

    Returns (block, outcome). block is None if the account was already blocked, and
    then nothing is asked of the service: use deactivate_account to ask again.
    outcome is None when deactivation is not configured.
    """
    reason = reason.strip() or DEFAULT_REASON
    block = block_user(actor, user, reason, remove_content=remove_content)
    if block is None or not deactivation_enabled():
        return block, None
    return block, deactivate_account(actor, user, reason, caused_by=block)


def deactivation_status(user):
    """The latest deactivation attempt since user was last blocked, or None."""
    block = (
        ModerationAction.objects.filter(action=Action.BLOCK_USER, subject_user=user)
        .order_by("-created_at", "-id")
        .first()
    )
    attempts = ModerationAction.objects.filter(
        action=Action.DEACTIVATE_ACCOUNT, subject_user=user
    )
    if block is not None:
        attempts = attempts.filter(created_at__gte=block.created_at)
    return attempts.order_by("-created_at", "-id").first()
