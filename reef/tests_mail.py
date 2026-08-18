# Copyright The IETF Trust 2026, All Rights Reserved
from django.core import mail
from django.test import SimpleTestCase, override_settings

from reef.mail import EmailMessage, make_message_id, send_mail


@override_settings(MESSAGE_ID_DOMAIN="example.org")
class SendMailTests(SimpleTestCase):
    def setUp(self):
        mail.outbox = []

    def test_a_single_recipient_may_be_a_bare_string(self):
        send_mail("doe@example.org", "Subject", "Body", "from@example.org")
        self.assertEqual(mail.outbox[0].to, ["doe@example.org"])

    def test_a_list_of_recipients(self):
        to = ["doe@example.org", "joe@example.org"]
        send_mail(to, "Subject", "Body", "from@example.org")
        self.assertEqual(mail.outbox[0].to, to)

    @override_settings(DEFAULT_FROM_EMAIL="reef@example.org")
    def test_from_defaults_to_the_configured_address(self):
        send_mail("doe@example.org", "Subject", "Body")
        self.assertEqual(mail.outbox[0].from_email, "reef@example.org")

    def test_subject_and_body_survive(self):
        send_mail("doe@example.org", "Subject", "Body", "from@example.org")
        self.assertEqual(mail.outbox[0].subject, "Subject")
        self.assertEqual(mail.outbox[0].body, "Body")


@override_settings(MESSAGE_ID_DOMAIN="example.org", REEF_SUBSCRIPTIONS_URL="")
class EmailMessageTests(SimpleTestCase):
    def test_message_id_uses_the_configured_domain(self):
        # Not the local hostname, which under Kubernetes is a pod name.
        self.assertTrue(make_message_id().endswith("@example.org>"))
        message = EmailMessage(subject="s", body="b", to=["doe@example.org"])
        self.assertTrue(
            message.extra_headers["Message-ID"].endswith("@example.org>"),
            message.extra_headers["Message-ID"],
        )

    def test_marked_as_automatically_generated(self):
        message = EmailMessage(subject="s", body="b", to=["doe@example.org"])
        self.assertEqual(message.extra_headers["Auto-Submitted"], "auto-generated")

    @override_settings(REEF_SUBSCRIPTIONS_URL="https://example.org/subscriptions")
    def test_list_unsubscribe_when_a_url_is_configured(self):
        message = EmailMessage(subject="s", body="b", to=["doe@example.org"])
        self.assertEqual(
            message.extra_headers["List-Unsubscribe"],
            "<https://example.org/subscriptions>",
        )
        # One-click unsubscribe needs an unauthenticated endpoint Reef has not
        # got, so the header that advertises one must stay off.
        self.assertNotIn("List-Unsubscribe-Post", message.extra_headers)

    def test_no_list_unsubscribe_without_a_url(self):
        message = EmailMessage(subject="s", body="b", to=["doe@example.org"])
        self.assertNotIn("List-Unsubscribe", message.extra_headers)

    @override_settings(REEF_SUBSCRIPTIONS_URL="https://example.org/subscriptions")
    def test_a_caller_keeps_its_own_headers(self):
        message = EmailMessage(
            subject="s",
            body="b",
            to=["doe@example.org"],
            headers={
                "message-id": "<given@example.org>",
                "Auto-Submitted": "no",
                "List-Unsubscribe": "<mailto:unsub@example.org>",
            },
        )
        self.assertNotIn("Message-ID", message.extra_headers)
        self.assertEqual(message.extra_headers["message-id"], "<given@example.org>")
        self.assertEqual(message.extra_headers["Auto-Submitted"], "no")
        self.assertEqual(
            message.extra_headers["List-Unsubscribe"], "<mailto:unsub@example.org>"
        )

    def test_the_headers_reach_the_sent_message(self):
        mail.outbox = []
        EmailMessage(subject="s", body="b", to=["doe@example.org"]).send()
        sent = mail.outbox[0].message()
        self.assertEqual(sent["Auto-Submitted"], "auto-generated")
        self.assertTrue(sent["Message-ID"].endswith("@example.org>"))
