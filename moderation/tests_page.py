# Copyright The IETF Trust 2026, All Rights Reserved
import json
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from docsets.models import DocumentSet
from reefauth.testing import login
from surveys.models import Response, Survey

from . import actions
from .admin import PAGE_SIZE
from .models import ModerationAction

User = get_user_model()

DEACTIVATE_URL = "https://accounts.example/deactivate"


class ModerationPageLayoutTests(TestCase):
    def setUp(self):
        staff = User.objects.create_superuser(username="mod", oidc_sub="mod")
        login(self.client, staff)
        self.owner = User.objects.create(username="o", oidc_sub="sub-o")
        self.url = reverse("admin:moderation")
        DocumentSet.objects.create(owner=self.owner, title="A set")
        survey = Survey.objects.create(slug="s", title="S", definition={})
        Response.objects.create(survey=survey, data={"q": "words"})

    def test_each_table_is_its_own_form_in_its_own_fieldset_with_a_decision(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'class="moderation-section"', count=3)
        self.assertContains(page, "<legend>Sets (1 unreviewed)</legend>")
        self.assertContains(page, "<legend>Survey responses (1 unreviewed)</legend>")
        self.assertContains(page, "Moderation decision", count=2)
        self.assertContains(page, 'id="reason_sets"')
        self.assertContains(page, 'id="reason_responses"')

    def test_a_decision_acts_only_on_the_rows_of_its_own_form(self):
        html = self.client.get(self.url).content.decode()
        sets_form = html.split("<legend>Sets")[1].split("</form>")[0]
        self.assertIn('name="sets"', sets_form)
        self.assertNotIn('name="responses"', sets_form)

    def test_the_selection_warning_shows_only_when_there_is_another_page(self):
        warning = "Using Previous or Next clears what is ticked"
        self.assertNotContains(self.client.get(self.url), warning)
        DocumentSet.objects.bulk_create(
            [DocumentSet(owner=self.owner, title=f"S{n}") for n in range(PAGE_SIZE)]
        )
        self.assertContains(self.client.get(self.url), warning, count=1)

    def test_acting_keeps_the_page_and_filter_you_were_on(self):
        document_set = DocumentSet.objects.get()
        response = self.client.post(
            f"{self.url}?flagged=1&sets_page=1",
            {"button": "fine", "sets": [document_set.pk], "reason": ""},
        )
        self.assertRedirects(
            response,
            f"{self.url}?flagged=1&sets_page=1",
            fetch_redirect_response=False,
        )

    def test_content_with_no_text_is_labelled_so_in_italics_and_stays_a_link(self):
        DocumentSet.objects.create(owner=self.owner, title="")
        survey = Survey.objects.get()
        Response.objects.create(survey=survey, data={"n": 3})
        page = self.client.get(self.url)
        self.assertContains(page, "<em>(no text)</em></a>", count=2)
        self.assertContains(page, 'aria-label="Select set (no text)"')

    def test_select_all_is_added_by_script_not_written_into_the_page(self):
        page = self.client.get(self.url)
        self.assertContains(page, "data-select-all", count=2)
        self.assertContains(page, "moderation-select-all.js")


@mock.patch.object(actions, "ACCOUNT_BLOCKING_ENABLED", True)
class BlockedAccountsDecisionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(username="mod", oidc_sub="mod")
        login(self.client, self.staff)
        self.spammer = User.objects.create(
            username="s", oidc_sub="sub-s", account_uuid=uuid.uuid4()
        )
        actions.block_user(self.staff, self.spammer, "blocked for pills")
        self.url = reverse("admin:moderation")

    @override_settings(REEF_ACCOUNT_DEACTIVATE_USER_URL=DEACTIVATE_URL)
    def test_deactivation_has_its_own_reason_field_and_unblock_keeps_its_own(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'id="deactivate_reason"')
        self.assertContains(page, 'id="unblock_reason"')
        self.assertContains(page, "Moderation decision", count=1)

    @override_settings(REEF_ACCOUNT_DEACTIVATE_USER_URL=None)
    def test_without_deactivation_none_of_its_controls_are_shown(self):
        page = self.client.get(self.url)
        self.assertNotContains(page, "deactivate_reason")
        self.assertNotContains(page, "Deactivate ticked")
        self.assertNotContains(page, "Account service</th>")
        self.assertContains(page, 'id="unblock_reason"')

    @override_settings(REEF_ACCOUNT_DEACTIVATE_USER_URL=DEACTIVATE_URL)
    def test_the_deactivation_reason_comes_from_its_own_field(self):
        reply = mock.MagicMock()
        reply.__enter__.return_value.status = 204
        reply.__enter__.return_value.read.return_value = b""
        with mock.patch("urllib.request.urlopen", return_value=reply) as urlopen:
            self.client.post(
                self.url,
                {
                    "button": "deactivate",
                    "users": [self.spammer.pk],
                    "reason": "the wrong box",
                    "deactivate_reason": "the right box",
                },
            )
        sent = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(sent["reason"], "the right box")
        self.assertFalse(
            ModerationAction.objects.filter(reason="the wrong box").exists()
        )
