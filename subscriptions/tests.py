# Copyright The IETF Trust 2026, All Rights Reserved
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from docsets.models import DocumentSet, DocumentSetEntry
from reef import rfcmeta
from reef.celery import app as celery_app
from reef.mail import EmailMessage
from reef.testing import stub_rfc_index
from subjects.models import Subject, SubjectAssignment

from .models import Subscription
from .tasks import (
    CONFIRMATION_SUBJECT,
    SendEmailError,
    digest_subject,
    send_subscription_confirmation,
    send_subscription_digest,
    subscriptions_for_document,
)

User = get_user_model()


class SubscriptionApiTests(APITestCase):
    def test_requires_auth(self):
        self.assertIn(
            self.client.get("/api/reef/subscriptions/").status_code, (401, 403)
        )

    def test_create_list_delete_scoped_to_user(self):
        user = User.objects.create(username="u", oidc_sub="s")
        other = User.objects.create(username="o", oidc_sub="s2")
        Subscription.objects.create(user=other, kind=Subscription.Kind.NEW_RFC)

        self.client.force_authenticate(user=user)
        create = self.client.post(
            "/api/reef/subscriptions/",
            {"kind": "new_rfc", "params": {}},
            format="json",
        )
        self.assertEqual(create.status_code, 201)

        listing = self.client.get("/api/reef/subscriptions/")
        self.assertEqual(len(listing.json()), 1)  # only own, not other's
        sub_id = listing.json()[0]["id"]

        delete = self.client.delete(f"/api/reef/subscriptions/{sub_id}/")
        self.assertEqual(delete.status_code, 204)
        self.assertFalse(Subscription.objects.filter(user=user).exists())

    def test_create_for_one_rfc(self):
        user = User.objects.create(username="u", oidc_sub="s")
        self.client.force_authenticate(user=user)

        create = self.client.post(
            "/api/reef/subscriptions/",
            {"kind": "rfc", "params": {"rfc": " RFC 9110 "}},
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        self.assertEqual(create.json()["params"], {"rfc": "rfc9110"})

    def test_rfc_kind_requires_an_rfc_param(self):
        user = User.objects.create(username="u", oidc_sub="s")
        self.client.force_authenticate(user=user)

        bad = (
            {},  # missing
            {"rfc": ""},
            {"rfc": 9110},  # not a string
            {"rfc": "9110"},  # no series prefix, so ambiguous
            {"rfc": "draft-ietf-httpbis-semantics"},  # not a series identifier
            {"rfc": "rfc" + "9" * 31},  # over DOC_ID_MAX_LENGTH
            {"rfc": "rfc9110", "source": "rfc-page"},  # unknown key
        )
        for params in bad:
            with self.subTest(params=params):
                response = self.client.post(
                    "/api/reef/subscriptions/",
                    {"kind": "rfc", "params": params},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
        self.assertFalse(Subscription.objects.exists())

    def test_params_must_be_an_object(self):
        user = User.objects.create(username="u", oidc_sub="s")
        self.client.force_authenticate(user=user)

        response = self.client.post(
            "/api/reef/subscriptions/",
            {"kind": "new_rfc", "params": ["9110"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_paramless_kind_rejects_params(self):
        user = User.objects.create(username="u", oidc_sub="s")
        self.client.force_authenticate(user=user)

        response = self.client.post(
            "/api/reef/subscriptions/",
            {"kind": "new_rfc", "params": {"rfc": "rfc9110"}},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class SubscriptionUniquenessTests(APITestCase):
    """One subscription per (user, kind, params), whichever way it is written."""

    def setUp(self):
        self.user = User.objects.create(username="u", oidc_sub="s")
        self.client.force_authenticate(user=self.user)

    def subscribe(self, rfc):
        return self.client.post(
            "/api/reef/subscriptions/",
            {"kind": "rfc", "params": {"rfc": rfc}},
            format="json",
        )

    def test_repeat_post_returns_the_existing_subscription(self):
        first = self.subscribe("rfc9110")
        second = self.subscribe("rfc9110")

        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(Subscription.objects.count(), 1)

    def test_spellings_of_one_rfc_collide(self):
        ids = ["rfc791", "RFC791", "rfc 791", "rfc-791", "rfc0791", " RFC 0791 "]
        for rfc in ids:
            with self.subTest(rfc=rfc):
                self.assertEqual(self.subscribe(rfc).status_code, 201)

        self.assertEqual(Subscription.objects.count(), 1)
        self.assertEqual(Subscription.objects.get().params, {"rfc": "rfc791"})

    def test_different_rfcs_are_different_subscriptions(self):
        self.subscribe("rfc791")
        self.subscribe("rfc9110")
        self.assertEqual(Subscription.objects.count(), 2)

    def test_each_user_gets_their_own(self):
        other = User.objects.create(username="o", oidc_sub="s2")
        Subscription.objects.create(
            user=other, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )

        self.assertEqual(self.subscribe("rfc9110").status_code, 201)
        self.assertEqual(Subscription.objects.count(), 2)

    def test_database_rejects_a_duplicate_written_directly(self):
        Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Subscription.objects.create(
                user=self.user,
                kind=Subscription.Kind.RFC,
                params={"rfc": "RFC 9110"},  # normalized by save(), so it collides
            )

    def test_case_does_not_defeat_the_constraint(self):
        Subscription.objects.create(
            user=self.user,
            kind=Subscription.Kind.BY_STATUS,
            params={"status": "proposed standard"},
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Subscription.objects.create(
                user=self.user,
                kind=Subscription.Kind.BY_STATUS,
                params={"status": "Proposed Standard"},
            )

    def test_model_save_normalizes_outside_the_api(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "RFC-0791"}
        )
        subscription.refresh_from_db()
        self.assertEqual(subscription.params, {"rfc": "rfc791"})

    def test_model_save_rejects_bad_params_outside_the_api(self):
        with self.assertRaises(ValidationError):
            Subscription.objects.create(
                user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "9110"}
            )


class SetSubscriptionTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username="u", oidc_sub="s")
        self.client.force_authenticate(user=self.user)
        self.set = DocumentSet.objects.create(owner=self.user, title="HTTP core")

    def subscribe(self, **body):
        return self.client.post("/api/reef/subscriptions/", body, format="json")

    def test_subscribe_to_a_set(self):
        response = self.subscribe(kind="set", set=self.set.pk)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["set"], str(self.set.pk))

    def test_set_kind_requires_a_set(self):
        self.assertEqual(self.subscribe(kind="set").status_code, 400)

    def test_other_kinds_reject_a_set(self):
        response = self.subscribe(kind="new_rfc", set=self.set.pk)
        self.assertEqual(response.status_code, 400)

    def test_cannot_subscribe_to_someone_elses_set(self):
        other = User.objects.create(username="o", oidc_sub="s2")
        theirs = DocumentSet.objects.create(owner=other, title="Theirs")
        # Readable, but not yet subscribable: a set that is not yours is, to
        # the subscription endpoint, the same as one that does not exist.
        self.assertEqual(self.subscribe(kind="set", set=theirs.pk).status_code, 400)
        unknown = str(uuid.uuid4())
        self.assertEqual(self.subscribe(kind="set", set=unknown).status_code, 400)
        self.assertEqual(self.subscribe(kind="set", set="not-a-uuid").status_code, 400)

    def test_cannot_subscribe_to_a_set_staff_have_taken_down(self):
        # The same 400 as a set that does not exist: the set is gone as far as
        # every read path is concerned, its owner's included.
        self.set.soft_delete("Spam.")
        self.assertEqual(self.subscribe(kind="set", set=self.set.pk).status_code, 400)
        self.assertFalse(Subscription.objects.exists())

    def test_a_subscription_to_a_deleted_set_is_neither_listed_nor_deletable(self):
        # Hidden, not deleted: the subscription comes back if the set is
        # restored. Listing it would be the one thing left saying the set is
        # there, and it names an id that 404s everywhere else.
        subscription_id = self.subscribe(kind="set", set=self.set.pk).json()["id"]
        self.set.soft_delete()

        self.assertEqual(self.client.get("/api/reef/subscriptions/").json(), [])
        self.assertEqual(
            self.client.delete(
                f"/api/reef/subscriptions/{subscription_id}/"
            ).status_code,
            404,
        )
        self.assertTrue(Subscription.objects.filter(pk=subscription_id).exists())

        self.set.restore()
        listing = self.client.get("/api/reef/subscriptions/").json()
        self.assertEqual([row["id"] for row in listing], [subscription_id])

    def test_deleting_a_set_does_not_hide_the_other_kinds(self):
        # Regression: the set filter runs over every subscription, and the
        # kinds that hold no set must not be caught by it.
        Subscription.objects.create(user=self.user, kind=Subscription.Kind.NEW_RFC)
        self.set.soft_delete()
        self.assertEqual(len(self.client.get("/api/reef/subscriptions/").json()), 1)

    def test_repeat_subscribe_to_a_set_is_idempotent(self):
        first = self.subscribe(kind="set", set=self.set.pk)
        second = self.subscribe(kind="set", set=self.set.pk)
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(Subscription.objects.count(), 1)

    def test_two_sets_are_two_subscriptions(self):
        second_set = DocumentSet.objects.create(owner=self.user, title="Other")
        self.subscribe(kind="set", set=self.set.pk)
        self.subscribe(kind="set", set=second_set.pk)
        self.assertEqual(Subscription.objects.count(), 2)

    def test_nulls_are_not_distinct_for_the_other_kinds(self):
        # Regression: adding a nullable column to the constraint would, with
        # Postgres' default NULLS DISTINCT, stop it catching duplicates of every
        # kind that has no set.
        Subscription.objects.create(user=self.user, kind=Subscription.Kind.NEW_RFC)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Subscription.objects.create(user=self.user, kind=Subscription.Kind.NEW_RFC)

    def test_deleting_a_set_deletes_its_subscriptions(self):
        self.subscribe(kind="set", set=self.set.pk)
        self.set.delete()
        self.assertFalse(Subscription.objects.exists())

    def test_model_save_enforces_the_set_rule(self):
        with self.assertRaises(ValidationError):
            Subscription.objects.create(user=self.user, kind=Subscription.Kind.SET)
        with self.assertRaises(ValidationError):
            Subscription.objects.create(
                user=self.user,
                kind=Subscription.Kind.NEW_RFC,
                document_set=self.set,
            )


class SubjectSubscriptionTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username="u", oidc_sub="s")
        self.client.force_authenticate(user=self.user)
        self.subject = Subject.objects.create(slug="security", name="Security")

    def subscribe(self, **body):
        return self.client.post("/api/reef/subscriptions/", body, format="json")

    def test_subscribe_to_a_subject(self):
        response = self.subscribe(kind="subject", subject=self.subject.pk)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["subject"], self.subject.pk)

    def test_subject_kind_requires_a_subject(self):
        self.assertEqual(self.subscribe(kind="subject").status_code, 400)

    def test_other_kinds_reject_a_subject(self):
        response = self.subscribe(kind="new_rfc", subject=self.subject.pk)
        self.assertEqual(response.status_code, 400)

    def test_a_subject_and_a_set_cannot_both_be_named(self):
        # The constraint is written as though a kind fills its own relation and
        # no other, so a row filling two would be compared against a shape
        # nothing else writes.
        document_set = DocumentSet.objects.create(owner=self.user, title="HTTP")
        response = self.subscribe(
            kind="subject", subject=self.subject.pk, set=document_set.pk
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("set", response.json())

    def test_anyone_can_subscribe_to_any_subject(self):
        # Unlike a set: the vocabulary is public and staff-curated, so there is
        # no owner for a subject to belong to.
        other = User.objects.create(username="o", oidc_sub="s2")
        self.client.force_authenticate(user=other)
        self.assertEqual(
            self.subscribe(kind="subject", subject=self.subject.pk).status_code, 201
        )

    def test_cannot_subscribe_to_a_subject_that_does_not_exist(self):
        self.assertEqual(self.subscribe(kind="subject", subject=9999).status_code, 400)

    def test_repeat_subscribe_to_a_subject_is_idempotent(self):
        first = self.subscribe(kind="subject", subject=self.subject.pk)
        second = self.subscribe(kind="subject", subject=self.subject.pk)
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(Subscription.objects.count(), 1)

    def test_two_subjects_are_two_subscriptions(self):
        routing = Subject.objects.create(slug="routing", name="Routing")
        self.subscribe(kind="subject", subject=self.subject.pk)
        self.subscribe(kind="subject", subject=routing.pk)
        self.assertEqual(Subscription.objects.count(), 2)

    def test_a_set_and_a_subject_subscription_do_not_collide(self):
        # Regression: two nullable relation columns in one constraint, with
        # NULLS NOT DISTINCT, must still tell a set subscription from a subject
        # one rather than seeing two rows of nulls.
        document_set = DocumentSet.objects.create(owner=self.user, title="HTTP")
        self.subscribe(kind="set", set=document_set.pk)
        self.subscribe(kind="subject", subject=self.subject.pk)
        self.assertEqual(Subscription.objects.count(), 2)

    def test_renaming_a_subject_keeps_its_subscribers(self):
        # The whole reason this is a relation and not a params key.
        subscription_id = self.subscribe(
            kind="subject", subject=self.subject.pk
        ).json()["id"]
        self.subject.slug = "security-and-privacy"
        self.subject.name = "Security and privacy"
        self.subject.save()
        listing = self.client.get("/api/reef/subscriptions/").json()
        self.assertEqual([row["id"] for row in listing], [subscription_id])

    def test_deleting_a_subject_deletes_its_subscriptions(self):
        self.subscribe(kind="subject", subject=self.subject.pk)
        self.subject.delete()
        self.assertFalse(Subscription.objects.exists())

    def test_model_save_enforces_the_subject_rule(self):
        with self.assertRaises(ValidationError):
            Subscription.objects.create(user=self.user, kind=Subscription.Kind.SUBJECT)
        with self.assertRaises(ValidationError):
            Subscription.objects.create(
                user=self.user,
                kind=Subscription.Kind.NEW_RFC,
                subject=self.subject,
            )


class DocumentMatchingTests(APITestCase):
    """subscriptions_for_document: the kinds that name a document.

    Matching consults Red's index to expand subseries, so these work from a stubbed
    index rather than the network.
    """

    def setUp(self):
        stub_rfc_index(self)
        self.user = User.objects.create(username="u", oidc_sub="s")
        self.set = DocumentSet.objects.create(owner=self.user, title="HTTP core")
        DocumentSetEntry.objects.create(document_set=self.set, doc="rfc9110")

    def test_matches_an_rfc_subscription(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        self.assertEqual(list(subscriptions_for_document("RFC 9110")), [subscription])

    def test_matches_through_a_set(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=self.set
        )
        self.assertEqual(list(subscriptions_for_document("rfc9110")), [subscription])

    def test_a_document_added_later_matches(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=self.set
        )
        self.assertEqual(list(subscriptions_for_document("bcp14")), [])
        DocumentSetEntry.objects.create(document_set=self.set, doc="bcp14")
        self.assertEqual(list(subscriptions_for_document("bcp14")), [subscription])

    def test_overlapping_subscriptions_are_each_returned_once(self):
        # Both match rfc9110 for the same user. Collapsing them into one mail is
        # the caller's job; the query must not lose or duplicate either.
        Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=self.set
        )
        self.assertEqual(subscriptions_for_document("rfc9110").count(), 2)

    def test_a_deleted_set_matches_nothing(self):
        # The join reaches the entry rows directly, so nothing excludes a
        # taken-down set unless this query does it.
        Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=self.set
        )
        self.set.soft_delete("Spam.")
        self.assertEqual(list(subscriptions_for_document("rfc9110")), [])
        self.set.restore()
        self.assertEqual(subscriptions_for_document("rfc9110").count(), 1)

    def test_matches_through_a_subject(self):
        subject = Subject.objects.create(slug="security", name="Security")
        SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SUBJECT, subject=subject
        )
        self.assertEqual(list(subscriptions_for_document("RFC 9110")), [subscription])

    def test_a_document_assigned_a_subject_later_matches(self):
        # The same moving membership a set has: subscribing to a subject covers
        # whatever carries it when the change lands.
        subject = Subject.objects.create(slug="security", name="Security")
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SUBJECT, subject=subject
        )
        self.assertEqual(list(subscriptions_for_document("bcp14")), [])
        SubjectAssignment.objects.create(subject=subject, doc="bcp14")
        self.assertEqual(list(subscriptions_for_document("bcp14")), [subscription])

    def test_unassigning_a_subject_stops_matching_it(self):
        subject = Subject.objects.create(slug="security", name="Security")
        assignment = SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SUBJECT, subject=subject
        )
        assignment.delete()
        self.assertEqual(list(subscriptions_for_document("rfc9110")), [])

    def test_a_subject_carrying_two_documents_matches_each_once(self):
        subject = Subject.objects.create(slug="security", name="Security")
        SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        SubjectAssignment.objects.create(subject=subject, doc="rfc8446")
        Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SUBJECT, subject=subject
        )
        self.assertEqual(subscriptions_for_document("rfc9110").count(), 1)
        self.assertEqual(subscriptions_for_document("rfc8446").count(), 1)

    def test_predicate_kinds_do_not_match_a_document(self):
        Subscription.objects.create(user=self.user, kind=Subscription.Kind.NEW_RFC)
        Subscription.objects.create(user=self.user, kind=Subscription.Kind.OBSOLETED)
        self.assertEqual(list(subscriptions_for_document("rfc9110")), [])

    def test_a_set_holding_a_subseries_matches_its_constituents(self):
        """BCP 14 is RFC 2119 plus RFC 8174, and somebody whose set holds bcp14 meant
        to hear about both."""
        DocumentSetEntry.objects.create(document_set=self.set, doc="bcp14")
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=self.set
        )
        self.assertEqual(list(subscriptions_for_document("rfc2119")), [subscription])

    def test_an_rfc_subscription_to_a_subseries_matches_its_constituents(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "bcp14"}
        )
        self.assertEqual(list(subscriptions_for_document("rfc2119")), [subscription])

    def test_a_subject_assigned_to_a_subseries_matches_its_constituents(self):
        subject = Subject.objects.create(name="Requirements", slug="requirements")
        SubjectAssignment.objects.create(subject=subject, doc="bcp14")
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SUBJECT, subject=subject
        )
        self.assertEqual(list(subscriptions_for_document("rfc2119")), [subscription])

    def test_a_document_in_no_subseries_matches_only_itself(self):
        DocumentSetEntry.objects.create(document_set=self.set, doc="bcp14")
        Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=self.set
        )
        self.assertEqual(list(subscriptions_for_document("rfc8446")), [])

    def test_a_subscriber_reached_two_ways_is_returned_once(self):
        """Directly and through the subseries: still one subscription."""
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "rfc2119"}
        )
        DocumentSetEntry.objects.create(document_set=self.set, doc="bcp14")
        Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=self.set
        )
        matched = list(subscriptions_for_document("rfc2119"))
        self.assertEqual(len(matched), 2)  # two subscriptions, not four rows
        self.assertIn(subscription, matched)

    def test_no_index_means_no_expansion_rather_than_an_error(self):
        """A real gap: the bcp14 subscriber misses a notification. Whether that
        should be retried belongs to ingest, which does not exist yet."""
        rfcmeta.clear_cache()
        DocumentSetEntry.objects.create(document_set=self.set, doc="bcp14")
        Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=self.set
        )
        with mock.patch("reef.rfcmeta._shared", return_value=None):
            with self.assertLogs("reef", level="WARNING"):
                self.assertEqual(list(subscriptions_for_document("rfc2119")), [])


class DigestSubjectTests(APITestCase):
    def test_one_document_is_named(self):
        self.assertEqual(
            digest_subject([{"doc": "rfc9110", "change": "Published"}]),
            "RFC series updates: RFC 9110",
        )

    def test_many_documents_are_counted(self):
        events = [{"doc": doc} for doc in ("rfc9110", "rfc9111", "bcp14")]
        self.assertEqual(digest_subject(events), "RFC series updates: 3 documents")

    def test_the_same_document_twice_counts_once(self):
        events = [{"doc": "rfc9110", "change": "a"}, {"doc": "rfc9110", "change": "b"}]
        self.assertEqual(digest_subject(events), "RFC series updates: RFC 9110")

    def test_events_with_no_document(self):
        # A predicate kind can match something that is not about one document.
        self.assertEqual(
            digest_subject([{"change": "BCP 14 now includes RFC 8174"}]),
            "RFC series updates",
        )


@override_settings(MESSAGE_ID_DOMAIN="example.org", REEF_SUBSCRIPTIONS_URL="")
class SendSubscriptionDigestTests(APITestCase):
    def setUp(self):
        mail.outbox = []
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="reader@example.org"
        )

    def test_one_mail_for_an_rfc_subscription(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        send_subscription_digest(
            subscription.user_id,
            [subscription.pk],
            [
                {
                    "doc": "rfc9110",
                    "change": "Obsoleted by RFC 9999",
                    "url": "https://www.rfc-editor.org/info/rfc9110",
                }
            ],
        )
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["reader@example.org"])
        self.assertEqual(sent.subject, "RFC series updates: RFC 9110")
        self.assertIn("changes to RFC 9110", sent.body)
        self.assertIn("RFC 9110: Obsoleted by RFC 9999", sent.body)
        self.assertIn("https://www.rfc-editor.org/info/rfc9110", sent.body)
        self.assertEqual(sent.extra_headers["Auto-Submitted"], "auto-generated")
        self.assertTrue(sent.extra_headers["Message-ID"].endswith("@example.org>"))

    def test_a_batch_is_one_mail_not_one_per_document(self):
        # The scenario plan.md calls out as unverifiable while nothing sent mail.
        document_set = DocumentSet.objects.create(owner=self.user, title="HTTP")
        for doc in ("rfc9110", "rfc9111", "rfc9112"):
            DocumentSetEntry.objects.create(document_set=document_set, doc=doc)
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=document_set
        )
        send_subscription_digest(
            subscription.user_id,
            [subscription.pk],
            [
                {"doc": doc, "change": "Published"}
                for doc in ("rfc9110", "rfc9111", "rfc9112")
            ],
        )
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.subject, "RFC series updates: 3 documents")
        # The sentence wraps, so look for the title rather than the whole phrase.
        self.assertIn('"HTTP"', sent.body)
        self.assertIn("your document set", sent.body)
        self.assertIn("There have been 3 changes", sent.body)
        for expected in ("RFC 9110", "RFC 9111", "RFC 9112"):
            self.assertIn(expected, sent.body)

    def test_prose_is_wrapped_for_plain_text(self):
        # A set title is up to 200 characters of the owner's choosing and a
        # change line comes from the datatracker feed, so neither can be
        # trusted to fit a line.
        document_set = DocumentSet.objects.create(
            owner=self.user, title="Everything the HTTP working group has ever " * 4
        )
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=document_set
        )
        send_subscription_digest(
            subscription.user_id,
            [subscription.pk],
            [{"doc": "rfc9110", "change": "Obsoleted by RFC 9999, " * 8}],
        )
        for line in mail.outbox[0].body.split("\n"):
            self.assertLessEqual(len(line), 78, line)

    def test_each_kind_says_what_was_subscribed_to(self):
        cases = [
            (Subscription.Kind.NEW_RFC, {}, "every new RFC"),
            (
                Subscription.Kind.BY_STATUS,
                {"status": "internet standard"},
                'status "internet standard"',
            ),
            (Subscription.Kind.OBSOLETED, {}, "obsoleted or made historic"),
        ]
        for kind, params, expected in cases:
            with self.subTest(kind=kind):
                mail.outbox = []
                subscription = Subscription.objects.create(
                    user=self.user, kind=kind, params=params
                )
                send_subscription_digest(
                    subscription.user_id,
                    [subscription.pk],
                    [{"doc": "rfc9110", "change": "Published"}],
                )
                self.assertIn(expected, mail.outbox[0].body)
                subscription.delete()

    def test_a_subject_subscription_names_the_subject(self):
        # The name, not the slug: the slug is for URLs and the reader is being
        # told in prose what they signed up for.
        subject = Subject.objects.create(slug="security", name="Security")
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SUBJECT, subject=subject
        )
        send_subscription_digest(
            subscription.user_id,
            [subscription.pk],
            [{"doc": "rfc9110", "change": "Published"}],
        )
        self.assertIn("on the subject of Security", mail.outbox[0].body)

    @override_settings(REEF_SUBSCRIPTIONS_URL="https://example.org/subscriptions")
    def test_the_reader_is_told_how_to_stop(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.NEW_RFC
        )
        send_subscription_digest(
            subscription.user_id, [subscription.pk], [{"doc": "rfc9110"}]
        )
        sent = mail.outbox[0]
        self.assertIn("https://example.org/subscriptions", sent.body)
        self.assertEqual(
            sent.extra_headers["List-Unsubscribe"],
            "<https://example.org/subscriptions>",
        )

    def test_nothing_is_sent_for_an_empty_batch(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.NEW_RFC
        )
        send_subscription_digest(subscription.user_id, [subscription.pk], [])
        self.assertEqual(mail.outbox, [])

    def test_a_deleted_subscription_is_not_an_error(self):
        # Unsubscribing is a hard delete, so it can happen between the match
        # and the send. Retrying would never find it.
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.NEW_RFC
        )
        pk = subscription.pk
        subscription.delete()
        send_subscription_digest(self.user.pk, [pk], [{"doc": "rfc9110"}])
        self.assertEqual(mail.outbox, [])

    def test_nothing_is_sent_once_the_set_has_been_taken_down(self):
        # A takedown between the match and the send. Really deleting the set
        # would have taken the subscription with it, and the digest would name
        # the set staff have just removed.
        document_set = DocumentSet.objects.create(owner=self.user, title="HTTP")
        DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=document_set
        )
        document_set.soft_delete("Spam.")
        send_subscription_digest(
            subscription.user_id, [subscription.pk], [{"doc": "rfc9110"}]
        )
        self.assertEqual(mail.outbox, [])

    def test_nothing_is_sent_without_an_address(self):
        user = User.objects.create(username="noaddr", oidc_sub="s2", email="")
        subscription = Subscription.objects.create(
            user=user, kind=Subscription.Kind.NEW_RFC
        )
        send_subscription_digest(
            subscription.user_id, [subscription.pk], [{"doc": "rfc9110"}]
        )
        self.assertEqual(mail.outbox, [])

    def test_a_send_failure_is_retryable(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.NEW_RFC
        )
        with mock.patch.object(EmailMessage, "send", side_effect=OSError("no relay")):
            with self.assertRaises(SendEmailError):
                send_subscription_digest(
                    subscription.user_id, [subscription.pk], [{"doc": "rfc9110"}]
                )


@override_settings(MESSAGE_ID_DOMAIN="example.org", REEF_SUBSCRIPTIONS_URL="")
class SendSubscriptionConfirmationTests(APITestCase):
    def setUp(self):
        mail.outbox = []
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="reader@example.org"
        )

    def test_says_what_was_subscribed_to_and_that_nothing_is_needed(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        send_subscription_confirmation(subscription.pk)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["reader@example.org"])
        self.assertEqual(sent.subject, CONFIRMATION_SUBJECT)
        self.assertIn("now subscribed to changes to RFC 9110", sent.body)
        # A courtesy, not a verification: a reader who has met double opt-in
        # elsewhere must not be left waiting for a link.
        self.assertIn("nothing to confirm", sent.body)
        self.assertEqual(sent.extra_headers["Auto-Submitted"], "auto-generated")

    def test_the_wording_is_shared_with_the_digest(self):
        # One include renders the sentence for both messages, so the same
        # subscription is described the same way in each.
        document_set = DocumentSet.objects.create(owner=self.user, title="HTTP")
        DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=document_set
        )
        send_subscription_confirmation(subscription.pk)
        send_subscription_digest(
            subscription.user_id, [subscription.pk], [{"doc": "rfc9110"}]
        )
        # Compared unwrapped, because the two leads are different lengths and
        # so the shared sentence breaks in different places. Only the lead
        # differs; the description of the subscription does not.
        phrase = 'changes to anything in your document set "HTTP".'
        for sent in mail.outbox:
            self.assertIn(phrase, " ".join(sent.body.split()))
        self.assertIn(
            "You are now subscribed to", " ".join(mail.outbox[0].body.split())
        )
        self.assertIn(
            "You asked to be notified about", " ".join(mail.outbox[1].body.split())
        )

    @override_settings(REEF_SUBSCRIPTIONS_URL="https://example.org/subscriptions")
    def test_the_reader_is_told_how_to_stop(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.NEW_RFC
        )
        send_subscription_confirmation(subscription.pk)
        sent = mail.outbox[0]
        self.assertIn("https://example.org/subscriptions", sent.body)
        self.assertEqual(
            sent.extra_headers["List-Unsubscribe"],
            "<https://example.org/subscriptions>",
        )

    def test_a_deleted_subscription_is_not_an_error(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.NEW_RFC
        )
        pk = subscription.pk
        subscription.delete()
        send_subscription_confirmation(pk)
        self.assertEqual(mail.outbox, [])

    def test_nothing_is_sent_once_the_set_has_been_taken_down(self):
        document_set = DocumentSet.objects.create(owner=self.user, title="HTTP")
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=document_set
        )
        document_set.soft_delete()
        send_subscription_confirmation(subscription.pk)
        self.assertEqual(mail.outbox, [])

    def test_nothing_is_sent_without_an_address(self):
        user = User.objects.create(username="noaddr", oidc_sub="s2", email="")
        subscription = Subscription.objects.create(
            user=user, kind=Subscription.Kind.NEW_RFC
        )
        send_subscription_confirmation(subscription.pk)
        self.assertEqual(mail.outbox, [])

    def test_a_send_failure_is_retryable(self):
        subscription = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.NEW_RFC
        )
        with mock.patch.object(EmailMessage, "send", side_effect=OSError("no relay")):
            with self.assertRaises(SendEmailError):
                send_subscription_confirmation(subscription.pk)


@override_settings(MESSAGE_ID_DOMAIN="example.org", REEF_SUBSCRIPTIONS_URL="")
class SubscribeSendsAConfirmationTests(APITestCase):
    """The create endpoint enqueues the confirmation, and only on a real create."""

    def setUp(self):
        mail.outbox = []
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="reader@example.org"
        )
        self.client.force_authenticate(user=self.user)
        # Run the enqueued task in this process. The point of these tests is
        # which enqueues happen, not that celery works.
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True
        self.addCleanup(
            celery_app.conf.update,
            task_always_eager=False,
            task_eager_propagates=False,
        )

    def subscribe(self, body):
        # on_commit, so the callback needs capturing inside the test's
        # transaction or it would never run.
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post("/api/reef/subscriptions/", body, format="json")

    def test_creating_a_subscription_sends_one_confirmation(self):
        response = self.subscribe({"kind": "rfc", "params": {"rfc": "rfc9110"}})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, CONFIRMATION_SUBJECT)
        self.assertIn("now subscribed to changes to RFC 9110", mail.outbox[0].body)

    def test_a_repeated_post_does_not_send_a_second_one(self):
        # Subscribing is idempotent, so a double click must not mail twice.
        body = {"kind": "rfc", "params": {"rfc": "rfc9110"}}
        self.assertEqual(self.subscribe(body).status_code, 201)
        self.assertEqual(self.subscribe(body).status_code, 201)
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_rejected_subscription_sends_nothing(self):
        response = self.subscribe({"kind": "rfc", "params": {}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(mail.outbox, [])


class DigestCoalescingTests(APITestCase):
    """One mail per reader, however many of their subscriptions matched.

    This is what the notification-volume open item was about, and it could only be
    fixed here: a task that sees one subscription cannot know the reader holds
    another covering the same document.
    """

    def setUp(self):
        self.user = User.objects.create(
            username="u", oidc_sub="s", email="reader@example.org"
        )
        self.set = DocumentSet.objects.create(owner=self.user, title="HTTP core")
        DocumentSetEntry.objects.create(document_set=self.set, doc="rfc9110")
        self.direct = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        self.through_set = Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=self.set
        )
        self.event = [{"doc": "rfc9110", "change": "Published"}]

    def test_two_matching_subscriptions_produce_one_mail(self):
        send_subscription_digest(
            self.user.pk, [self.direct.pk, self.through_set.pk], self.event
        )
        self.assertEqual(len(mail.outbox), 1)

    def test_the_mail_gives_every_reason_it_arrived(self):
        send_subscription_digest(
            self.user.pk, [self.direct.pk, self.through_set.pk], self.event
        )
        body = " ".join(mail.outbox[0].body.split())
        self.assertIn("changes to RFC 9110", body)
        self.assertIn('changes to anything in your document set "HTTP core"', body)

    def test_one_reason_still_reads_as_a_sentence(self):
        send_subscription_digest(self.user.pk, [self.direct.pk], self.event)
        body = " ".join(mail.outbox[0].body.split())
        self.assertIn("You asked to be notified about changes to RFC 9110.", body)

    def test_several_reasons_read_as_a_list(self):
        """Because "about changes to RFC 9110 and about anything in your document
        set" runs out of breath at three."""
        send_subscription_digest(
            self.user.pk, [self.direct.pk, self.through_set.pk], self.event
        )
        self.assertIn("You asked to be notified about:", mail.outbox[0].body)
        self.assertIn("  - changes to RFC 9110", mail.outbox[0].body)

    def test_a_subscription_belonging_to_somebody_else_is_ignored(self):
        other = User.objects.create(
            username="o", oidc_sub="o", email="other@example.org"
        )
        theirs = Subscription.objects.create(user=other, kind=Subscription.Kind.NEW_RFC)
        send_subscription_digest(self.user.pk, [self.direct.pk, theirs.pk], self.event)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["reader@example.org"])
        self.assertNotIn("every new RFC", mail.outbox[0].body)

    def test_a_taken_down_set_is_dropped_from_the_reasons(self):
        self.set.deleted_at = timezone.now()
        self.set.save()
        send_subscription_digest(
            self.user.pk, [self.direct.pk, self.through_set.pk], self.event
        )
        body = " ".join(mail.outbox[0].body.split())
        self.assertIn("changes to RFC 9110", body)
        self.assertNotIn("HTTP core", body)

    def test_nothing_is_sent_when_every_reason_has_gone(self):
        self.set.deleted_at = timezone.now()
        self.set.save()
        self.direct.delete()
        with self.assertLogs("reef", level="INFO"):
            send_subscription_digest(
                self.user.pk, [self.direct.pk, self.through_set.pk], self.event
            )
        self.assertEqual(mail.outbox, [])

    def test_the_phrase_has_no_space_before_its_full_stop(self):
        """The phrase template must not end with a newline: callers put the stop
        straight after the include, and a newline there wordwraps into " ."."""
        send_subscription_digest(self.user.pk, [self.direct.pk], self.event)
        self.assertNotIn(" .", " ".join(mail.outbox[0].body.split()))
