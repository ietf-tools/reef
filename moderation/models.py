# Copyright The IETF Trust 2026, All Rights Reserved
from django.conf import settings
from django.db import models


class ModerationAction(models.Model):
    """One thing a moderator did, kept for as long as the log is.

    Append-only: a row is never edited, and nothing here is a foreign key to the
    thing acted on, so the record outlives a hard delete of its target. The
    snapshot is the content as it was, because a takedown that is later reviewed
    or questioned has to be explainable after the content itself is gone.

    Nothing is ever told to the person acted on. They can ask, and whoever
    answers reads this.
    """

    class Action(models.TextChoices):
        TAKE_DOWN_SET = "take_down_set", "Take down set"
        RESTORE_SET = "restore_set", "Restore set"
        WITHDRAW_SURVEY = "withdraw_survey", "Withdraw survey"
        RESTORE_SURVEY = "restore_survey", "Restore survey"
        DELETE_RESPONSE = "delete_response", "Delete response"
        RESTORE_RESPONSE = "restore_response", "Restore response"
        DELETE_RATING = "delete_rating", "Delete rating"
        RESTORE_RATING = "restore_rating", "Restore rating"
        BLOCK_USER = "block_user", "Block account"
        UNBLOCK_USER = "unblock_user", "Unblock account"
        DEACTIVATE_ACCOUNT = "deactivate_account", "Deactivate in account service"

    # Copied alongside the key: the staff account can be deleted, and the log
    # still has to say who acted.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    actor_name = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=32, choices=Action.choices, db_index=True)
    target_type = models.CharField(max_length=32)
    target_id = models.CharField(max_length=64, db_index=True)
    # The account whose content or access this concerns, when there is one. The
    # subject identifier is copied for the same reason as actor_name.
    subject_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    subject_sub = models.CharField(max_length=255, blank=True, db_index=True)
    subject_name = models.CharField(max_length=255, blank=True)
    reason = models.TextField(blank=True)
    snapshot = models.JSONField(default=dict, blank=True)
    # Set on what an account block did to the account's content, so that
    # unblocking restores exactly that and nothing a moderator removed separately.
    caused_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consequences",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_action_display()} {self.target_type} {self.target_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("The moderator log is append-only.")
        super().save(*args, **kwargs)


class ModerationFlag(models.Model):
    """A moderator's verdict on a piece of content, kept as a labelled example.

    The labels are the point: they are the in-house dataset that says whether any
    classifier works on this content, and a score from one can sit on the same row
    later. A verdict of spam also takes the content down, through the logged
    actions; a verdict of fine only records that someone looked.

    The text is copied, as the log's snapshot is, so the label stays usable after
    the content is purged.
    """

    class Verdict(models.TextChoices):
        SPAM = "spam", "Spam"
        FINE = "fine", "Fine"

    target_type = models.CharField(max_length=32)
    target_id = models.CharField(max_length=64)
    subject_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    text = models.TextField()
    # Blank for a human label. A classifier that scores the same content writes
    # its own row, so its scores and the human verdict never overwrite each other.
    classifier = models.CharField(max_length=64, blank=True)
    score = models.FloatField(null=True, blank=True)
    # Whether the score was at or above the threshold when it was recorded, and so
    # put this in front of moderators. False for every score kept in shadow mode.
    raised = models.BooleanField(default=False, db_index=True)
    verdict = models.CharField(
        max_length=8, choices=Verdict.choices, blank=True, db_index=True
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["target_type", "target_id", "classifier"],
                name="one_flag_per_target_and_classifier",
            )
        ]

    def __str__(self):
        return f"{self.verdict or 'unreviewed'}: {self.target_type} {self.target_id}"
