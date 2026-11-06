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

from . import backfill, classify, jev
from .models import ClassifierFailure, ModerationFlag
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


@override_settings(REEF_JEV_API_KEY="k")
class ScoreTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(username="o", oidc_sub="sub-o", name="Olive")
        self.set = DocumentSet.objects.create(
            owner=self.owner, title="Cheap pills", description="buy now"
        )

    def test_the_score_is_recorded_with_who_owns_it(self):
        with mock.patch(URLOPEN, return_value=answer(0.97)):
            flag = classify.score("documentset", self.set.pk)
        self.assertEqual(flag.score, 0.97)
        self.assertEqual(flag.verdict, "")
        self.assertEqual(flag.classifier, jev.CLASSIFIER)
        self.assertEqual(flag.subject_user, self.owner)

    def test_a_score_at_the_threshold_is_flagged_and_below_it_is_not(self):
        with mock.patch(URLOPEN, side_effect=[answer(0.4), answer(0.39)]):
            self.assertTrue(classify.score("documentset", self.set.pk).flagged)
            self.set.title = "Changed"
            self.set.save()
            self.assertFalse(classify.score("documentset", self.set.pk).flagged)

    def test_a_human_label_is_never_flagged_by_its_score(self):
        label = ModerationFlag.objects.create(
            target_type="documentset", target_id="x", text="t", score=0.99
        )
        self.assertFalse(label.flagged)

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

    def test_a_response_scores_on_its_free_text_and_a_textless_one_scores_zero(self):
        survey = Survey.objects.create(slug="s", title="S", definition={})
        words = Response.objects.create(survey=survey, data={"q": "visit spam.example"})
        numbers = Response.objects.create(survey=survey, data={"n": 3})
        with mock.patch(URLOPEN, return_value=answer(0.9)) as urlopen:
            self.assertIsNotNone(classify.score("response", words.pk))
            self.assertEqual(classify.score("response", numbers.pk).score, 0.0)
        urlopen.assert_called_once()
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


@override_settings(REEF_JEV_API_KEY="k")
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
        with mock.patch("moderation.backfill.classify_content.delay") as delay:
            call_command("classify_unscored", limit=10, stdout=io.StringIO())
        delay.assert_called_once_with("documentset", str(self.waiting.pk))
        with mock.patch("moderation.backfill.classify_content.delay") as delay:
            call_command("classify_unscored", limit=0, stdout=io.StringIO())
        delay.assert_not_called()

    @override_settings(REEF_JEV_API_KEY=None)
    def test_without_a_key_it_refuses(self):
        with self.assertRaises(CommandError):
            call_command("classify_unscored", limit=1)


@override_settings(REEF_JEV_API_KEY="k")
class EmptyContentTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(username="o", oidc_sub="sub-o")

    def test_content_with_no_text_is_scored_zero_without_asking_the_classifier(self):
        empty = DocumentSet.objects.create(owner=self.owner, title="")
        with mock.patch(URLOPEN) as urlopen:
            flag = classify.score("documentset", empty.pk)
        urlopen.assert_not_called()
        self.assertEqual(flag.score, 0.0)
        self.assertFalse(flag.flagged)

    def test_a_batch_full_of_empty_sets_does_not_hide_the_rest(self):
        for _ in range(3):
            DocumentSet.objects.create(owner=self.owner, title="")
        survey = Survey.objects.create(title="S", definition={})
        Response.objects.create(survey=survey, data={"q": "real text"})
        with mock.patch(URLOPEN, return_value=answer(0.2)):
            for _ in range(2):
                with mock.patch("moderation.backfill.BATCH_SIZE", 3):
                    with mock.patch(
                        "moderation.backfill.classify_content.delay",
                        side_effect=lambda *args: classify.score(*args),
                    ):
                        queued, remaining = backfill.queue_unscored(3)
        self.assertEqual((queued, remaining), (1, 0))
        self.assertEqual(ModerationFlag.objects.count(), 4)

    def test_items_that_failed_do_not_take_batch_slots_from_the_rest(self):
        stuck = [
            DocumentSet.objects.create(owner=self.owner, title="x") for _ in range(3)
        ]
        fresh = DocumentSet.objects.create(owner=self.owner, title="fresh")
        for document_set in stuck:
            ClassifierFailure.objects.create(
                target_type="documentset",
                target_id=str(document_set.pk),
                detail="the classifier answered HTTP 400",
            )
        with mock.patch("moderation.backfill.classify_content.delay") as delay:
            queued, remaining = backfill.queue_unscored(3)
        delay.assert_called_once_with("documentset", str(fresh.pk))
        self.assertEqual((queued, remaining), (1, 0))

    def test_text_added_later_is_scored_for_real(self):
        document_set = DocumentSet.objects.create(owner=self.owner, title="")
        classify.score("documentset", document_set.pk)
        document_set.title = "Pills"
        document_set.save()
        with mock.patch(URLOPEN, return_value=answer(0.9)):
            flag = classify.score("documentset", document_set.pk)
        self.assertEqual(flag.score, 0.9)


@override_settings(REEF_JEV_API_KEY="k")
class PageScoreTests(TestCase):
    def setUp(self):
        staff = User.objects.create_superuser(username="mod", oidc_sub="mod")
        login(self.client, staff)
        owner = User.objects.create(username="o", oidc_sub="sub-o")
        self.flagged = DocumentSet.objects.create(owner=owner, title="Pills")
        self.quiet = DocumentSet.objects.create(owner=owner, title="RFC notes")
        for document_set, score in ((self.flagged, 0.95), (self.quiet, 0.04)):
            ModerationFlag.objects.create(
                target_type="documentset",
                target_id=str(document_set.pk),
                classifier=jev.CLASSIFIER,
                text="t",
                score=score,
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


@override_settings(REEF_JEV_API_KEY="k")
class ManualCheckTests(TestCase):
    def setUp(self):
        staff = User.objects.create_superuser(username="mod", oidc_sub="mod")
        login(self.client, staff)
        owner = User.objects.create(username="o", oidc_sub="sub-o")
        self.set = DocumentSet.objects.create(owner=owner, title="Pills")
        survey = Survey.objects.create(slug="s", title="S", definition={})
        self.response = Response.objects.create(survey=survey, data={"q": "visit x"})
        self.url = reverse("admin:moderation")

    def button(self, target_type, pk):
        return f"check:{target_type}:{pk}"

    def test_an_unscored_item_has_a_check_button_and_a_scored_one_does_not(self):
        page = self.client.get(self.url)
        self.assertContains(page, self.button("documentset", self.set.pk))
        self.assertContains(page, self.button("response", self.response.pk))
        ModerationFlag.objects.create(
            target_type="documentset",
            target_id=str(self.set.pk),
            classifier=jev.CLASSIFIER,
            text="t",
            score=0.2,
        )
        page = self.client.get(self.url)
        self.assertNotContains(page, self.button("documentset", self.set.pk))
        self.assertContains(page, self.button("response", self.response.pk))

    @override_settings(REEF_JEV_API_KEY=None)
    def test_with_no_classifier_there_are_no_buttons(self):
        self.assertNotContains(self.client.get(self.url), "check:documentset:")

    def test_the_button_scores_the_item_and_reports_it(self):
        with mock.patch(URLOPEN, return_value=answer(0.93)):
            page = self.client.post(
                self.url,
                {"button": self.button("documentset", self.set.pk)},
                follow=True,
            )
        self.assertEqual(ModerationFlag.objects.get().score, 0.93)
        self.assertContains(page, "Scored 0.93.")
        self.assertNotContains(page, self.button("documentset", self.set.pk))

    def test_a_response_can_be_checked_too(self):
        with mock.patch(URLOPEN, return_value=answer(0.4)):
            self.client.post(
                self.url, {"button": self.button("response", self.response.pk)}
            )
        self.assertEqual(ModerationFlag.objects.get().target_type, "response")

    def test_a_refusal_is_shown_and_leaves_the_button(self):
        with mock.patch(URLOPEN, side_effect=http_error(401)):
            page = self.client.post(
                self.url,
                {"button": self.button("documentset", self.set.pk)},
                follow=True,
            )
        self.assertContains(page, "HTTP 401")
        self.assertEqual(ModerationFlag.objects.count(), 0)
        self.assertContains(page, self.button("documentset", self.set.pk))

    def test_an_unreachable_service_is_shown(self):
        with mock.patch(URLOPEN, side_effect=urllib.error.URLError("down")):
            page = self.client.post(
                self.url,
                {"button": self.button("documentset", self.set.pk)},
                follow=True,
            )
        self.assertContains(page, "could not be reached")

    def test_a_malformed_button_does_nothing(self):
        with mock.patch(URLOPEN) as urlopen:
            for value in (
                "check:documentset:not-a-uuid",
                "check:response:x",
                "check:user:1",
                "check:",
                "check",
            ):
                with self.subTest(value=value):
                    response = self.client.post(self.url, {"button": value})
                    self.assertEqual(response.status_code, 302)
        urlopen.assert_not_called()


@override_settings(REEF_JEV_API_KEY="k")
class CheckAllUnscoredTests(TestCase):
    url = "/admin/moderation/"

    def setUp(self):
        staff = User.objects.create_superuser(username="mod", oidc_sub="mod")
        login(self.client, staff)
        self.owner = User.objects.create(username="o", oidc_sub="sub-o")

    def post(self):
        with mock.patch("moderation.backfill.classify_content.delay") as delay:
            page = self.client.post(self.url, {"button": "check_unscored"}, follow=True)
        return page, delay

    def test_it_queues_a_batch_and_says_how_many_remain(self):
        for number in range(5):
            DocumentSet.objects.create(owner=self.owner, title=f"T{number}")
        with mock.patch("moderation.backfill.BATCH_SIZE", 3):
            page, delay = self.post()
        self.assertEqual(delay.call_count, 3)
        self.assertContains(page, "Queued 3 for scoring; 2 more have no score yet.")

    def test_scored_items_are_left_alone(self):
        scored = DocumentSet.objects.create(owner=self.owner, title="Scored")
        ModerationFlag.objects.create(
            target_type="documentset",
            target_id=str(scored.pk),
            classifier=jev.CLASSIFIER,
            text="t",
            score=0.1,
        )
        page, delay = self.post()
        delay.assert_not_called()
        self.assertContains(page, "Everything already has a score.")

    def test_a_broker_failure_is_shown(self):
        DocumentSet.objects.create(owner=self.owner, title="T")
        with mock.patch(
            "moderation.backfill.classify_content.delay", side_effect=OSError("down")
        ):
            page = self.client.post(self.url, {"button": "check_unscored"}, follow=True)
        self.assertContains(page, "The scoring queue could not be reached.")

    def test_the_button_is_shown_only_with_a_classifier(self):
        self.assertContains(self.client.get(self.url), "Check all unscored")
        with override_settings(REEF_JEV_API_KEY=None):
            self.assertNotContains(self.client.get(self.url), "Check all unscored")


@override_settings(REEF_JEV_API_KEY="k")
class FailureVisibilityTests(TestCase):
    url = "/admin/moderation/"

    def setUp(self):
        staff = User.objects.create_superuser(username="mod", oidc_sub="mod")
        login(self.client, staff)
        owner = User.objects.create(username="o", oidc_sub="sub-o")
        self.set = DocumentSet.objects.create(owner=owner, title="T", description="d")

    def run_task(self, side_effect):
        with (
            mock.patch(URLOPEN, side_effect=side_effect),
            mock.patch.object(classify_content, "retry", side_effect=Retry()),
        ):
            classify_content.apply(args=("documentset", str(self.set.pk)))

    def test_an_unreachable_classifier_is_shown_on_the_page_with_the_reason(self):
        self.run_task(urllib.error.URLError("name resolution failed"))
        page = self.client.get(self.url)
        self.assertContains(page, "1 item could not be scored")
        self.assertContains(page, "name resolution failed")

    def test_a_refusal_is_shown_with_its_status(self):
        self.run_task(http_error(401))
        self.assertContains(self.client.get(self.url), "answered HTTP 401")

    def test_repeated_failures_count_attempts_on_one_row(self):
        self.run_task(http_error(429))
        self.run_task(http_error(429))
        self.assertEqual(ClassifierFailure.objects.get().attempts, 2)

    def test_a_later_success_clears_the_notice(self):
        self.run_task(http_error(429))
        with mock.patch(URLOPEN, return_value=answer(0.1)):
            classify_content.apply(args=("documentset", str(self.set.pk)))
        self.assertEqual(ClassifierFailure.objects.count(), 0)
        self.assertNotContains(self.client.get(self.url), "could not be scored")

    def test_nothing_from_the_content_is_recorded(self):
        self.set.title = "buy-pills-title"
        self.set.save()
        self.run_task(http_error(401))
        self.assertNotIn("buy-pills", ClassifierFailure.objects.get().detail)
