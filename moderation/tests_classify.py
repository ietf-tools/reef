# Copyright The IETF Trust 2026, All Rights Reserved
import io
import json
import urllib.error
from unittest import mock

from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from docsets.models import DocumentSet
from reefauth.testing import login
from surveys.models import Response, Survey

from . import classify, jev
from .models import ModerationFlag
from .tasks import classify_content

User = get_user_model()

URLOPEN = "urllib.request.urlopen"


def answer(probability):
    body = json.dumps({"answers": {"spam": {"type": "noul", "noul": probability}}})
    reply = mock.MagicMock()
    reply.__enter__.return_value = io.BytesIO(body.encode())
    return reply


def http_error(code):
    return urllib.error.HTTPError(jev.ENDPOINT, code, "x", {}, io.BytesIO(b"echo"))


class JevClientTests(TestCase):
    def test_the_request_carries_the_text_a_fixed_question_and_the_key_only(self):
        with mock.patch(URLOPEN, return_value=answer(0.9)) as urlopen:
            self.assertEqual(jev.spam_probability("buy pills", "k"), 0.9)
        (request,), kwargs = urlopen.call_args
        self.assertEqual(request.full_url, jev.ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), "Bearer k")
        self.assertEqual(kwargs["timeout"], jev.TIMEOUT_SECONDS)
        self.assertEqual(
            json.loads(request.data),
            {
                "model": jev.MODEL,
                "state": "buy pills",
                "questions": {
                    "spam": {"type": "noul", "instructions": jev.INSTRUCTIONS}
                },
            },
        )

    def test_long_text_is_cut(self):
        with mock.patch(URLOPEN, return_value=answer(0.1)) as urlopen:
            jev.spam_probability("x" * (jev.MAX_TEXT_CHARS + 5), "k")
        sent = json.loads(urlopen.call_args.args[0].data)["state"]
        self.assertEqual(len(sent), jev.MAX_TEXT_CHARS)

    def test_an_unexpected_answer_is_an_error_not_a_score(self):
        for body in (
            {},
            {"answers": {}},
            {"answers": {"spam": {"noul": 2}}},
            {"answers": {"spam": {"noul": "high"}}},
            {"answers": {"spam": {"noul": True}}},
        ):
            reply = mock.MagicMock()
            reply.__enter__.return_value = io.BytesIO(json.dumps(body).encode())
            with self.subTest(body=body), mock.patch(URLOPEN, return_value=reply):
                with self.assertRaises(ValueError):
                    jev.spam_probability("t", "k")


@override_settings(REEF_JEV_API_KEY="k", REEF_SPAM_FLAG_THRESHOLD=None)
class ScoreTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(username="o", oidc_sub="sub-o", name="Olive")
        self.set = DocumentSet.objects.create(
            owner=self.owner, title="Cheap pills", description="buy now"
        )

    def test_no_threshold_records_the_score_without_raising_it(self):
        with mock.patch(URLOPEN, return_value=answer(0.97)):
            flag = classify.score("documentset", self.set.pk)
        self.assertEqual(flag.score, 0.97)
        self.assertFalse(flag.raised)
        self.assertEqual(flag.verdict, "")
        self.assertEqual(flag.classifier, jev.CLASSIFIER)
        self.assertEqual(flag.subject_user, self.owner)

    @override_settings(REEF_SPAM_FLAG_THRESHOLD=0.8)
    def test_a_score_at_the_threshold_is_raised_and_below_it_is_not(self):
        with mock.patch(URLOPEN, side_effect=[answer(0.8), answer(0.79)]):
            self.assertTrue(classify.score("documentset", self.set.pk).raised)
            self.set.title = "Changed"
            self.set.save()
            self.assertFalse(classify.score("documentset", self.set.pk).raised)

    def test_the_payload_has_no_trace_of_who_wrote_it(self):
        with mock.patch(URLOPEN, return_value=answer(0.1)) as urlopen:
            classify.score("documentset", self.set.pk)
        sent = urlopen.call_args.args[0].data.decode()
        for private in ("Olive", "sub-o", str(self.set.pk)):
            self.assertNotIn(private, sent)

    def test_unchanged_text_is_not_scored_twice_and_edited_text_is(self):
        with mock.patch(URLOPEN, side_effect=[answer(0.5), answer(0.6)]) as urlopen:
            classify.score("documentset", self.set.pk)
            self.assertIsNone(classify.score("documentset", self.set.pk))
            self.assertEqual(urlopen.call_count, 1)
            self.set.description = "something else"
            self.set.save()
            classify.score("documentset", self.set.pk)
            self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(ModerationFlag.objects.count(), 1)

    def test_a_response_scores_on_its_free_text_and_a_textless_one_is_skipped(self):
        survey = Survey.objects.create(slug="s", title="S", definition={})
        words = Response.objects.create(survey=survey, data={"q": "visit spam.example"})
        numbers = Response.objects.create(survey=survey, data={"n": 3})
        with mock.patch(URLOPEN, return_value=answer(0.9)) as urlopen:
            self.assertIsNotNone(classify.score("response", words.pk))
            self.assertIsNone(classify.score("response", numbers.pk))
        self.assertEqual(
            json.loads(urlopen.call_args.args[0].data)["state"], "visit spam.example"
        )

    def test_removed_content_is_not_sent(self):
        self.set.soft_delete("spam")
        with mock.patch(URLOPEN) as urlopen:
            self.assertIsNone(classify.score("documentset", self.set.pk))
        urlopen.assert_not_called()

    def test_a_human_label_and_the_score_live_side_by_side(self):
        ModerationFlag.objects.create(
            target_type="documentset",
            target_id=str(self.set.pk),
            text="t",
            verdict="fine",
        )
        with mock.patch(URLOPEN, return_value=answer(0.99)):
            classify.score("documentset", self.set.pk)
        self.assertEqual(ModerationFlag.objects.count(), 2)


@override_settings(REEF_JEV_API_KEY="k", REEF_SPAM_FLAG_THRESHOLD=None)
class TaskTests(TestCase):
    def setUp(self):
        owner = User.objects.create(username="o", oidc_sub="sub-o")
        self.set = DocumentSet.objects.create(owner=owner, title="T", description="d")

    def run_task(self, **effect):
        return classify_content.apply(args=("documentset", str(self.set.pk)))

    def test_it_records_a_score(self):
        with mock.patch(URLOPEN, return_value=answer(0.3)):
            classify_content("documentset", str(self.set.pk))
        self.assertEqual(ModerationFlag.objects.get().score, 0.3)

    @override_settings(REEF_JEV_API_KEY=None)
    def test_with_no_key_nothing_is_sent(self):
        with mock.patch(URLOPEN) as urlopen:
            classify_content("documentset", str(self.set.pk))
        urlopen.assert_not_called()

    def assertRetried(self, error):
        with (
            mock.patch(URLOPEN, side_effect=error),
            mock.patch.object(classify_content, "retry", side_effect=Retry()) as retry,
        ):
            with self.assertRaises(Retry):
                classify_content.run("documentset", str(self.set.pk))
        retry.assert_called_once()

    def test_overload_and_rate_limits_are_retried(self):
        for code in (429, 529, 500):
            with self.subTest(code=code):
                self.assertRetried(http_error(code))

    def test_a_network_failure_is_retried(self):
        self.assertRetried(urllib.error.URLError("down"))

    def test_a_refusal_is_logged_not_retried_and_leaves_no_flag(self):
        for code in (401, 422):
            with self.subTest(code=code):
                with (
                    mock.patch(URLOPEN, side_effect=http_error(code)),
                    mock.patch.object(classify_content, "retry") as retry,
                    self.assertLogs("reef", "ERROR") as logs,
                ):
                    classify_content.run("documentset", str(self.set.pk))
                retry.assert_not_called()
                self.assertNotIn("echo", " ".join(logs.output))
        self.assertEqual(ModerationFlag.objects.count(), 0)

    def test_an_unreadable_answer_leaves_no_flag(self):
        reply = mock.MagicMock()
        reply.__enter__.return_value = io.BytesIO(b"{}")
        with mock.patch(URLOPEN, return_value=reply), self.assertLogs("reef", "ERROR"):
            classify_content.run("documentset", str(self.set.pk))
        self.assertEqual(ModerationFlag.objects.count(), 0)


class SignalTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(username="o", oidc_sub="sub-o")

    @override_settings(REEF_JEV_API_KEY="k")
    def test_a_new_set_and_a_new_response_are_queued_after_commit(self):
        with mock.patch("moderation.tasks.classify_content.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                document_set = DocumentSet.objects.create(owner=self.owner, title="T")
                survey = Survey.objects.create(slug="s", title="S", definition={})
                response = Response.objects.create(survey=survey, data={"q": "x"})
        delay.assert_any_call("documentset", str(document_set.pk))
        delay.assert_any_call("response", str(response.pk))

    @override_settings(REEF_JEV_API_KEY="k")
    def test_adding_a_document_to_a_set_does_not_queue_it_but_retitling_does(self):
        document_set = DocumentSet.objects.create(owner=self.owner, title="T")
        with mock.patch("moderation.tasks.classify_content.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                document_set.save(update_fields=["updated_at"])
            delay.assert_not_called()
            with self.captureOnCommitCallbacks(execute=True):
                document_set.title = "New"
                document_set.save()
            delay.assert_called_once()

    @override_settings(REEF_JEV_API_KEY=None)
    def test_with_no_key_nothing_is_queued(self):
        with mock.patch("moderation.tasks.classify_content.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                DocumentSet.objects.create(owner=self.owner, title="T")
        delay.assert_not_called()

    @override_settings(REEF_JEV_API_KEY="k")
    def test_a_broker_failure_does_not_fail_the_write(self):
        with (
            mock.patch(
                "moderation.tasks.classify_content.delay",
                side_effect=OSError("no broker"),
            ),
            self.assertLogs("reef", "WARNING"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                DocumentSet.objects.create(owner=self.owner, title="T")
        self.assertEqual(DocumentSet.objects.count(), 1)


class BackfillCommandTests(TestCase):
    def setUp(self):
        owner = User.objects.create(username="o", oidc_sub="sub-o")
        self.scored = DocumentSet.objects.create(owner=owner, title="Scored")
        self.waiting = DocumentSet.objects.create(owner=owner, title="Waiting")
        ModerationFlag.objects.create(
            target_type="documentset",
            target_id=str(self.scored.pk),
            classifier=jev.CLASSIFIER,
            text="t",
            score=0.1,
        )

    @override_settings(REEF_JEV_API_KEY="k")
    def test_it_queues_only_what_has_no_score_and_respects_the_limit(self):
        with mock.patch(
            "moderation.management.commands.classify_unscored.classify_content.delay"
        ) as delay:
            call_command("classify_unscored", limit=10, stdout=io.StringIO())
        delay.assert_called_once_with("documentset", str(self.waiting.pk))
        with mock.patch(
            "moderation.management.commands.classify_unscored.classify_content.delay"
        ) as delay:
            call_command("classify_unscored", limit=0, stdout=io.StringIO())
        delay.assert_not_called()

    @override_settings(REEF_JEV_API_KEY=None)
    def test_without_a_key_it_refuses(self):
        with self.assertRaises(CommandError):
            call_command("classify_unscored", limit=1)


@override_settings(REEF_JEV_API_KEY="k")
class PageScoreTests(TestCase):
    def setUp(self):
        staff = User.objects.create_superuser(username="mod", oidc_sub="mod")
        login(self.client, staff)
        owner = User.objects.create(username="o", oidc_sub="sub-o")
        self.flagged = DocumentSet.objects.create(owner=owner, title="Pills")
        self.quiet = DocumentSet.objects.create(owner=owner, title="RFC notes")
        for document_set, score, raised in (
            (self.flagged, 0.95, True),
            (self.quiet, 0.04, False),
        ):
            ModerationFlag.objects.create(
                target_type="documentset",
                target_id=str(document_set.pk),
                classifier=jev.CLASSIFIER,
                text="t",
                score=score,
                raised=raised,
            )

    def test_scores_are_shown_and_flagged_ones_marked(self):
        page = self.client.get(reverse("admin:moderation"))
        self.assertContains(page, "0.95")
        self.assertContains(page, "0.04")
        self.assertContains(page, "<strong>flagged</strong>", count=1)

    def test_the_flagged_only_view_hides_the_rest_and_keeps_other_params(self):
        page = self.client.get(reverse("admin:moderation"), {"flagged": "1"})
        self.assertContains(page, "Pills")
        self.assertNotContains(page, "RFC notes")
        self.assertContains(page, "Show everything")

    @override_settings(REEF_JEV_API_KEY=None)
    def test_with_no_classifier_the_page_has_no_score_column(self):
        page = self.client.get(reverse("admin:moderation"))
        self.assertNotContains(page, "Spam score")
