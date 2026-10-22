# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from docsets.models import DocumentSet, DocumentSetEntry
from reef.testing_admin import ReadOnlyAdminChecks
from reefauth.testing import login
from surveys.models import Response, Survey

from . import actions
from .models import ModerationAction

User = get_user_model()
Action = ModerationAction.Action


def make_staff():
    return User.objects.create_superuser(
        username="mod", oidc_sub="mod", name="Mo Derator"
    )


def make_survey(slug="sat"):
    return Survey.objects.create(
        slug=slug,
        title="Satisfaction",
        definition={"pages": []},
        status=Survey.Status.PUBLISHED,
    )


class ModerationLogTests(TestCase):
    def test_a_row_cannot_be_edited(self):
        entry = actions.log_action(None, Action.BLOCK_USER, "user", 1)
        entry.reason = "changed"
        with self.assertRaises(ValueError):
            entry.save()

    def test_the_log_outlives_the_people_in_it(self):
        staff = make_staff()
        spammer = User.objects.create(username="s", oidc_sub="sub-s", name="Sam Spam")
        entry = actions.log_action(
            staff, Action.BLOCK_USER, "user", spammer.pk, subject_user=spammer
        )
        spammer.delete()
        staff.delete()
        entry.refresh_from_db()
        self.assertEqual(entry.subject_sub, "sub-s")
        self.assertEqual(entry.subject_name, "Sam Spam")
        self.assertEqual(entry.actor_name, "Mo Derator")
        self.assertIsNone(entry.subject_user)
        self.assertIsNone(entry.actor)


class SetTakedownTests(APITestCase):
    def setUp(self):
        self.staff = make_staff()
        self.owner = User.objects.create(username="o", oidc_sub="sub-o")
        self.set = DocumentSet.objects.create(
            owner=self.owner, title="Buy pills", description="http://spam.example"
        )
        DocumentSetEntry.objects.create(document_set=self.set, doc="rfc9110")

    def test_taking_a_set_down_logs_what_it_said(self):
        entry = actions.take_down_set(self.staff, self.set, "spam")
        self.assertEqual(entry.action, Action.TAKE_DOWN_SET)
        self.assertEqual(entry.target_id, str(self.set.pk))
        self.assertEqual(entry.subject_user, self.owner)
        self.assertEqual(entry.reason, "spam")
        self.assertEqual(entry.snapshot["title"], "Buy pills")
        self.assertEqual(entry.snapshot["documents"], ["rfc9110"])

    def test_a_taken_down_set_404s_for_everyone(self):
        actions.take_down_set(self.staff, self.set)
        url = f"/api/reef/sets/{self.set.pk}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_taking_down_twice_logs_once(self):
        actions.take_down_set(self.staff, self.set)
        self.assertIsNone(
            actions.take_down_set(
                self.staff, DocumentSet.all_objects.get(pk=self.set.pk)
            )
        )
        self.assertEqual(ModerationAction.objects.count(), 1)

    def test_restore_logs_and_brings_it_back(self):
        actions.take_down_set(self.staff, self.set)
        deleted = DocumentSet.all_objects.get(pk=self.set.pk)
        entry = actions.restore_set(self.staff, deleted)
        self.assertEqual(entry.action, Action.RESTORE_SET)
        self.assertEqual(
            self.client.get(f"/api/reef/sets/{self.set.pk}/").status_code, 200
        )


class ResponseRemovalTests(TestCase):
    def setUp(self):
        self.staff = make_staff()
        self.survey = make_survey()
        self.user = User.objects.create(username="u", oidc_sub="sub-u")
        self.response = Response.objects.create(
            survey=self.survey, data={"q": "cheap pills"}, submitted_by=self.user
        )
        self.other = Response.objects.create(survey=self.survey, data={"q": "fine"})

    def test_a_removed_response_is_gone_from_every_default_read(self):
        actions.delete_responses(
            self.staff, Response.objects.filter(pk=self.response.pk), "spam"
        )
        self.assertEqual(list(Response.objects.all()), [self.other])
        self.assertEqual(self.survey.responses.count(), 1)
        self.assertEqual(Response.all_objects.count(), 2)

    def test_each_removal_is_logged_with_the_answers(self):
        (entry,) = actions.delete_responses(
            self.staff, Response.objects.filter(pk=self.response.pk), "spam"
        )
        self.assertEqual(entry.action, Action.DELETE_RESPONSE)
        self.assertEqual(entry.subject_user, self.user)
        self.assertEqual(entry.snapshot["data"], {"q": "cheap pills"})
        self.assertEqual(entry.snapshot["survey"], "sat")

    def test_removing_what_is_already_removed_logs_nothing(self):
        actions.delete_responses(
            self.staff, Response.objects.filter(pk=self.response.pk)
        )
        self.assertEqual(
            actions.delete_responses(self.staff, Response.all_objects.all()).__len__(),
            1,
        )

    def test_restore_counts_it_again(self):
        actions.delete_responses(
            self.staff, Response.objects.filter(pk=self.response.pk)
        )
        actions.restore_responses(self.staff, Response.all_objects.all())
        self.assertEqual(Response.objects.count(), 2)
        self.assertEqual(
            ModerationAction.objects.filter(action=Action.RESTORE_RESPONSE).count(), 1
        )


class BlockTests(TestCase):
    def setUp(self):
        self.staff = make_staff()
        self.spammer = User.objects.create(username="s", oidc_sub="sub-s")
        self.set = DocumentSet.objects.create(owner=self.spammer, title="Pills")
        self.survey = make_survey()
        self.response = Response.objects.create(
            survey=self.survey, data={}, submitted_by=self.spammer
        )

    def test_block_alone_keeps_content(self):
        block = actions.block_user(self.staff, self.spammer, "spam")
        self.spammer.refresh_from_db()
        self.assertTrue(self.spammer.is_blocked)
        self.assertEqual(self.spammer.blocked_reason, "spam")
        self.assertEqual(self.spammer.blocked_by, self.staff)
        self.assertEqual(block.action, Action.BLOCK_USER)
        self.assertEqual(DocumentSet.objects.count(), 1)
        self.assertEqual(Response.objects.count(), 1)

    def test_block_and_remove_takes_the_content_down_and_logs_it_under_the_block(self):
        block = actions.block_user(
            self.staff, self.spammer, "spam", remove_content=True
        )
        self.assertEqual(DocumentSet.objects.count(), 0)
        self.assertEqual(Response.objects.count(), 0)
        self.assertEqual(block.consequences.count(), 2)

    def test_unblock_restores_only_what_the_block_removed(self):
        earlier = DocumentSet.objects.create(owner=self.spammer, title="Removed first")
        actions.take_down_set(self.staff, earlier, "separately")
        actions.block_user(self.staff, self.spammer, "spam", remove_content=True)
        actions.unblock_user(self.staff, self.spammer)
        self.spammer.refresh_from_db()
        self.assertFalse(self.spammer.is_blocked)
        self.assertEqual(list(DocumentSet.objects.all()), [self.set])
        self.assertEqual(Response.objects.count(), 1)
        self.assertEqual(DocumentSet.all_objects.count(), 2)

    def test_unblock_is_logged(self):
        actions.block_user(self.staff, self.spammer)
        entry = actions.unblock_user(self.staff, self.spammer, "mistake")
        self.assertEqual(entry.action, Action.UNBLOCK_USER)
        self.assertEqual(entry.reason, "mistake")

    def test_staff_cannot_be_blocked(self):
        other = User.objects.create_superuser(username="o", oidc_sub="o")
        with self.assertRaises(actions.CannotBlock):
            actions.block_user(self.staff, other)

    def test_you_cannot_block_yourself(self):
        me = User.objects.create(username="me", oidc_sub="me")
        with self.assertRaises(actions.CannotBlock):
            actions.block_user(me, me)

    def test_blocking_twice_logs_once(self):
        actions.block_user(self.staff, self.spammer)
        self.assertIsNone(actions.block_user(self.staff, self.spammer))
        self.assertEqual(
            ModerationAction.objects.filter(action=Action.BLOCK_USER).count(), 1
        )


class ModerationAdminTests(ReadOnlyAdminChecks, TestCase):
    def setUp(self):
        self.staff = make_staff()
        login(self.client, self.staff)
        self.spammer = User.objects.create(username="s", oidc_sub="sub-s")

    def run_action(self, model, name, pks, reason="because"):
        url = reverse(
            f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist"
        )
        data = {"action": name, ACTION_CHECKBOX_NAME: pks}
        asked = self.client.post(url, data)
        self.assertEqual(asked.status_code, 200)
        self.assertEqual(ModerationAction.objects.count(), 0)
        return self.client.post(url, {**data, "confirm": "1", "reason": reason})

    def test_the_log_renders_and_is_read_only(self):
        entry = actions.log_action(self.staff, Action.BLOCK_USER, "user", 1)
        self.assertAdminRenders(entry)
        self.assertAdminReadOnly(entry)

    def test_blocking_from_the_user_list_asks_for_a_reason_then_logs_it(self):
        response = self.run_action(User, "block_accounts", [self.spammer.pk])
        self.assertEqual(response.status_code, 302)
        self.spammer.refresh_from_db()
        self.assertTrue(self.spammer.is_blocked)
        entry = ModerationAction.objects.get()
        self.assertEqual(entry.reason, "because")
        self.assertEqual(entry.actor, self.staff)

    def test_a_refused_block_changes_nothing(self):
        self.run_action(User, "block_accounts", [self.spammer.pk, self.staff.pk])
        self.spammer.refresh_from_db()
        self.assertFalse(self.spammer.is_blocked)
        self.assertEqual(ModerationAction.objects.count(), 0)

    def test_responses_are_deleted_through_the_action_and_never_for_real(self):
        survey = make_survey()
        response = Response.objects.create(survey=survey, data={"q": "x"})
        self.assertAdminRenders(response)
        self.assertEqual(
            self.client.post(
                self._admin_url(response, "delete", response.pk)
            ).status_code,
            403,
        )
        self.run_action(Response, "delete_responses", [response.pk])
        self.assertEqual(Response.objects.count(), 0)
        self.assertEqual(Response.all_objects.count(), 1)
        self.assertEqual(ModerationAction.objects.get().action, Action.DELETE_RESPONSE)

    def test_a_takedown_made_on_the_change_form_is_logged(self):
        owner = User.objects.create(username="o", oidc_sub="sub-o")
        document_set = DocumentSet.objects.create(owner=owner, title="Pills")
        url = self._admin_url(document_set, "change", document_set.pk)
        form = {
            "owner": owner.pk,
            "title": "Pills",
            "description": "",
            "deleted_at_0": timezone.now().strftime("%Y-%m-%d"),
            "deleted_at_1": "12:00:00",
            "deleted_reason": "spam",
            "entries-TOTAL_FORMS": "0",
            "entries-INITIAL_FORMS": "0",
            "entries-MIN_NUM_FORMS": "0",
            "entries-MAX_NUM_FORMS": "1000",
        }
        self.client.post(url, form)
        entry = ModerationAction.objects.get()
        self.assertEqual(entry.action, Action.TAKE_DOWN_SET)
        self.assertEqual(entry.reason, "spam")
        self.client.post(
            url, {**form, "deleted_at_0": "", "deleted_at_1": "", "deleted_reason": ""}
        )
        self.assertEqual(
            list(
                ModerationAction.objects.order_by("id").values_list("action", flat=True)
            ),
            [Action.TAKE_DOWN_SET, Action.RESTORE_SET],
        )
