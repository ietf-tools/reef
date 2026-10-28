# Copyright The IETF Trust 2026, All Rights Reserved
import io
import json
import urllib.error
import uuid
from unittest import mock

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from docsets.models import DocumentSet
from reefauth import deactivation
from reefauth.claims import sync_user_from_claims
from reefauth.testing import ACCESS_TOKEN, login

from . import actions
from .actions import Deactivation
from .models import ModerationAction

User = get_user_model()
Action = ModerationAction.Action

URLOPEN = "urllib.request.urlopen"
DEACTIVATE_URL = "https://account.example/app/api/service/deactivate-user"

configured = override_settings(REEF_ACCOUNT_DEACTIVATE_USER_URL=DEACTIVATE_URL)


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = io.BytesIO(json.dumps(body).encode())
        self.status = status

    def read(self, *args):
        return self._body.read(*args)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def account_service(deactivate=None):
    """A urlopen that plays the deactivate endpoint.

    deactivate is either a FakeResponse or an exception to raise.
    """
    calls = []

    def urlopen(request, timeout=None):
        calls.append(request)
        reply = FakeResponse({}, 200) if deactivate is None else deactivate
        if isinstance(reply, Exception):
            raise reply
        return reply

    urlopen.calls = calls
    return urlopen


def refusal(code, body):
    return urllib.error.HTTPError(
        DEACTIVATE_URL, code, "x", {}, io.BytesIO(body.encode())
    )


class AccountUuidClaimTests(TestCase):
    def test_the_claim_is_stored_on_a_new_user_and_on_a_later_token(self):
        account = uuid.uuid4()
        user = sync_user_from_claims({"sub": "s1"})
        self.assertIsNone(user.account_uuid)
        user = sync_user_from_claims({"sub": "s1", "account_uuid": str(account)})
        self.assertEqual(user.account_uuid, account)

    def test_a_token_without_the_claim_does_not_erase_it(self):
        account = uuid.uuid4()
        sync_user_from_claims({"sub": "s1", "account_uuid": str(account)})
        user = sync_user_from_claims({"sub": "s1", "name": "Ada"})
        self.assertEqual(user.account_uuid, account)

    def test_something_that_is_not_a_uuid_is_ignored(self):
        for junk in ("", "nope", None, 5):
            with self.subTest(junk=junk):
                user = sync_user_from_claims({"sub": "s2", "account_uuid": junk})
                self.assertIsNone(user.account_uuid)

    def test_two_subjects_may_share_an_account(self):
        account = str(uuid.uuid4())
        sync_user_from_claims({"sub": "a", "account_uuid": account})
        sync_user_from_claims({"sub": "b", "account_uuid": account})
        self.assertEqual(User.objects.filter(account_uuid=account).count(), 2)


class ClientTests(TestCase):
    def test_the_deactivate_request_carries_the_uuid_and_reason_under_a_bearer(self):
        urlopen = account_service()
        account = uuid.uuid4()
        with mock.patch(URLOPEN, urlopen):
            status, body = deactivation.deactivate(
                DEACTIVATE_URL, "tok", account, "Spam", body_limit=100
            )
        self.assertEqual((status, body), (200, "{}"))
        (request,) = urlopen.calls
        self.assertEqual(request.get_header("Authorization"), "Bearer tok")
        self.assertEqual(
            json.loads(request.data), {"uuid": str(account), "reason": "Spam"}
        )


@configured
class DeactivateAccountTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(username="mod", oidc_sub="mod")
        self.account = uuid.uuid4()
        self.user = User.objects.create(
            username="s", oidc_sub="sub-s", account_uuid=self.account
        )

    def run_block(self, urlopen, reason="pills", token="tok", **kwargs):
        with mock.patch(URLOPEN, urlopen):
            return actions.block_and_deactivate(
                self.staff, self.user, reason, token=token, **kwargs
            )

    def test_success_is_logged_under_the_block(self):
        urlopen = account_service()
        block, outcome = self.run_block(urlopen)
        self.assertEqual(outcome.status, Deactivation.DEACTIVATED)
        row = ModerationAction.objects.get(action=Action.DEACTIVATE_ACCOUNT)
        self.assertEqual(row.caused_by, block)
        self.assertEqual(row.snapshot["status"], "deactivated")
        self.assertEqual(row.subject_user, self.user)
        (call,) = urlopen.calls
        self.assertEqual(call.get_header("Authorization"), "Bearer tok")
        self.assertEqual(
            json.loads(call.data), {"uuid": str(self.account), "reason": "pills"}
        )

    def test_a_blank_reason_becomes_the_default_everywhere(self):
        urlopen = account_service()
        self.run_block(urlopen, reason="  ")
        self.assertEqual(json.loads(urlopen.calls[0].data)["reason"], "Spam")
        self.user.refresh_from_db()
        self.assertEqual(self.user.blocked_reason, "Spam")

    def test_no_account_uuid_asks_nobody_and_says_so(self):
        self.user.account_uuid = None
        self.user.save()
        urlopen = account_service()
        block, outcome = self.run_block(urlopen)
        self.assertEqual(outcome.status, Deactivation.NO_ACCOUNT_UUID)
        self.assertEqual(urlopen.calls, [])
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_blocked)

    def test_no_token_in_the_session_asks_nobody_and_says_so(self):
        for token in (None, ""):
            with self.subTest(token=token):
                self.user.blocked_at = None
                self.user.save()
                urlopen = account_service()
                block, outcome = self.run_block(urlopen, token=token)
                self.assertEqual(outcome.status, Deactivation.NO_TOKEN)
                self.assertEqual(urlopen.calls, [])
                self.user.refresh_from_db()
                self.assertTrue(self.user.is_blocked)

    def test_the_token_is_never_logged(self):
        self.run_block(account_service(), token="secret-token")
        for row in ModerationAction.objects.all():
            self.assertNotIn("secret-token", json.dumps(row.snapshot))
            self.assertNotIn("secret-token", row.reason)

    def test_a_refusal_keeps_the_block_and_the_services_words(self):
        urlopen = account_service(deactivate=refusal(403, "protected account"))
        block, outcome = self.run_block(urlopen)
        self.assertEqual(
            (outcome.status, outcome.http_status, outcome.detail),
            (Deactivation.REFUSED, 403, "protected account"),
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_blocked)
        row = ModerationAction.objects.get(action=Action.DEACTIVATE_ACCOUNT)
        self.assertEqual(row.snapshot["http_status"], 403)

    def test_what_is_kept_of_an_answer_is_cut(self):
        urlopen = account_service(deactivate=refusal(400, "x" * 5000))
        _, outcome = self.run_block(urlopen)
        self.assertEqual(len(outcome.detail), actions.DETAIL_LENGTH)

    def test_success_keeps_what_the_service_said_cut_to_length(self):
        _, outcome = self.run_block(
            account_service(deactivate=FakeResponse({"event": "x" * 5000}))
        )
        self.assertEqual(outcome.status, Deactivation.DEACTIVATED)
        self.assertTrue(outcome.detail.startswith('{"event": "xxx'))
        self.assertEqual(len(outcome.detail), actions.DETAIL_LENGTH)
        row = ModerationAction.objects.get(action=Action.DEACTIVATE_ACCOUNT)
        self.assertEqual(row.snapshot["detail"], outcome.detail)

    def test_an_unreachable_service_keeps_the_block_and_says_why(self):
        with self.assertLogs("reef", "WARNING"):
            _, outcome = self.run_block(
                account_service(deactivate=urllib.error.URLError("name not known"))
            )
        self.assertEqual(outcome.status, Deactivation.UNREACHABLE)
        self.assertIn("URLError", outcome.detail)
        self.assertIn("name not known", outcome.detail)
        row = ModerationAction.objects.get(action=Action.DEACTIVATE_ACCOUNT)
        self.assertIn("name not known", row.snapshot["detail"])
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_blocked)

    @override_settings(REEF_ACCOUNT_DEACTIVATE_USER_URL=None)
    def test_not_configured_blocks_locally_and_asks_nobody(self):
        urlopen = account_service()
        block, outcome = self.run_block(urlopen)
        self.assertIsNotNone(block)
        self.assertIsNone(outcome)
        self.assertEqual(urlopen.calls, [])

    def test_an_account_already_blocked_is_not_asked_again_by_a_block(self):
        self.run_block(account_service())
        urlopen = account_service()
        block, outcome = self.run_block(urlopen)
        self.assertEqual((block, outcome), (None, None))
        self.assertEqual(urlopen.calls, [])

    def test_asking_again_is_logged_and_the_latest_attempt_is_the_status(self):
        self.run_block(account_service(deactivate=urllib.error.URLError("down")))
        self.assertEqual(
            actions.deactivation_status(self.user).snapshot["status"], "unreachable"
        )
        with mock.patch(URLOPEN, account_service()):
            actions.deactivate_account(self.staff, self.user, "pills", token="tok")
        self.assertEqual(
            actions.deactivation_status(self.user).snapshot["status"], "deactivated"
        )

    def test_a_new_block_forgets_the_old_blocks_attempts(self):
        self.run_block(account_service())
        actions.unblock_user(self.staff, self.user)
        self.assertIsNotNone(actions.deactivation_status(self.user))
        with mock.patch(URLOPEN, account_service(deactivate=refusal(403, "no"))):
            actions.block_and_deactivate(self.staff, self.user, "again", token="tok")
        self.assertEqual(
            actions.deactivation_status(self.user).snapshot["status"], "refused"
        )

    def test_unblocking_never_calls_the_service(self):
        self.run_block(account_service())
        urlopen = account_service()
        with mock.patch(URLOPEN, urlopen):
            actions.unblock_user(self.staff, self.user)
        self.assertEqual(urlopen.calls, [])


@configured
class DeactivationInTheAdminTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(username="mod", oidc_sub="mod")
        login(self.client, self.staff)
        self.account = uuid.uuid4()
        self.spammer = User.objects.create(
            username="s", oidc_sub="sub-s", account_uuid=self.account
        )
        self.users_url = reverse("admin:reefauth_user_changelist")
        self.page_url = reverse("admin:moderation")

    def confirm(self, action, reason="pills", **extra):
        return self.client.post(
            self.users_url,
            {
                "action": action,
                ACTION_CHECKBOX_NAME: [self.spammer.pk],
                "confirm": "1",
                "reason": reason,
                **extra,
            },
            follow=True,
        )

    def test_the_block_form_makes_the_reason_required_and_offers_the_default(self):
        form = self.client.post(
            self.users_url,
            {"action": "block_accounts", ACTION_CHECKBOX_NAME: [self.spammer.pk]},
        )
        self.assertContains(form, "Spam")
        page = self.confirm("block_accounts", reason="")
        self.spammer.refresh_from_db()
        self.assertFalse(self.spammer.is_blocked)
        self.assertContains(page, "This field is required")

    def test_blocking_from_the_list_says_how_the_account_service_answered(self):
        with mock.patch(URLOPEN, account_service()):
            page = self.confirm("block_accounts")
        self.assertContains(page, "was deactivated in the account service")
        self.spammer.refresh_from_db()
        self.assertTrue(self.spammer.is_blocked)

    def test_a_refusal_is_shown_with_the_services_words(self):
        with mock.patch(URLOPEN, account_service(deactivate=refusal(403, "protected"))):
            page = self.confirm("block_accounts")
        self.assertContains(page, "refused to deactivate")
        self.assertContains(page, "HTTP 403")
        self.assertContains(page, "protected")

    def test_the_moderators_session_token_is_the_bearer(self):
        urlopen = account_service()
        with mock.patch(URLOPEN, urlopen):
            self.confirm("block_accounts")
        (call,) = urlopen.calls
        self.assertEqual(call.get_header("Authorization"), f"Bearer {ACCESS_TOKEN}")

    def test_a_session_without_a_token_blocks_and_tells_the_moderator(self):
        session = self.client.session
        del session["oidc_access_token"]
        session.save()
        urlopen = account_service()
        with mock.patch(URLOPEN, urlopen):
            page = self.confirm("block_accounts")
        self.assertContains(page, "holds no account service token")
        self.assertEqual(urlopen.calls, [])
        self.spammer.refresh_from_db()
        self.assertTrue(self.spammer.is_blocked)

    def test_a_401_suggests_signing_in_again(self):
        with mock.patch(URLOPEN, account_service(deactivate=refusal(401, "expired"))):
            page = self.confirm("block_accounts")
        self.assertContains(page, "sign out and in again")

    def test_an_account_with_no_id_is_blocked_and_the_moderator_is_told(self):
        self.spammer.account_uuid = None
        self.spammer.save()
        urlopen = account_service()
        with mock.patch(URLOPEN, urlopen):
            page = self.confirm("block_accounts")
        self.assertContains(page, "no account id on file yet")
        self.assertEqual(urlopen.calls, [])

    def test_the_page_block_button_sends_the_default_reason_when_blank(self):
        document_set = DocumentSet.objects.create(owner=self.spammer, title="Pills")
        urlopen = account_service()
        with mock.patch(URLOPEN, urlopen):
            page = self.client.post(
                self.page_url,
                {"button": "block", "sets": [document_set.pk], "reason": ""},
                follow=True,
            )
        self.assertContains(page, "was deactivated in the account service")
        self.assertEqual(json.loads(urlopen.calls[0].data)["reason"], "Spam")

    def test_the_blocked_table_shows_the_status_and_can_ask_again(self):
        with mock.patch(
            URLOPEN, account_service(deactivate=urllib.error.URLError("x"))
        ):
            self.confirm("block_accounts")
        page = self.client.get(self.page_url)
        self.assertContains(page, "could not be reached")
        urlopen = account_service()
        with mock.patch(URLOPEN, urlopen):
            page = self.client.post(
                self.page_url,
                {"button": "deactivate", "users": [self.spammer.pk], "reason": ""},
                follow=True,
            )
        self.assertContains(page, "was deactivated in the account service")
        self.spammer.refresh_from_db()
        self.assertEqual(
            json.loads(urlopen.calls[0].data)["reason"], self.spammer.blocked_reason
        )
        self.assertContains(page, "deactivated")

    def test_unblock_has_its_own_reason(self):
        actions.block_user(self.staff, self.spammer, "spam")
        self.client.post(
            self.page_url,
            {
                "button": "unblock",
                "users": [self.spammer.pk],
                "reason": "Spam",
                "unblock_reason": "mistake",
            },
        )
        row = ModerationAction.objects.get(action=Action.UNBLOCK_USER)
        self.assertEqual(row.reason, "mistake")

    @override_settings(REEF_ACCOUNT_DEACTIVATE_USER_URL=None)
    def test_not_configured_says_so_and_hides_the_deactivation_controls(self):
        page = self.confirm("block_accounts")
        self.assertContains(page, "not configured")
        page = self.client.get(self.page_url)
        self.assertNotContains(page, "Account service</th>")
        self.assertNotContains(page, "Deactivate selected again")
