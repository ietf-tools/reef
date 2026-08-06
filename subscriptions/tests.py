# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from rest_framework.test import APITestCase

from docsets.models import DocumentSet, DocumentSetEntry

from .models import Subscription
from .tasks import subscriptions_for_document

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
        self.assertEqual(response.json()["set"], self.set.pk)

    def test_set_kind_requires_a_set(self):
        self.assertEqual(self.subscribe(kind="set").status_code, 400)

    def test_other_kinds_reject_a_set(self):
        response = self.subscribe(kind="new_rfc", set=self.set.pk)
        self.assertEqual(response.status_code, 400)

    def test_cannot_subscribe_to_someone_elses_set(self):
        other = User.objects.create(username="o", oidc_sub="s2")
        theirs = DocumentSet.objects.create(
            owner=other, title="Theirs", visibility=DocumentSet.Visibility.PUBLIC
        )
        # Public, but not yet subscribable: a set that is not yours reads the
        # same as one that does not exist.
        self.assertEqual(self.subscribe(kind="set", set=theirs.pk).status_code, 400)
        self.assertEqual(self.subscribe(kind="set", set=9999).status_code, 400)

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


class DocumentMatchingTests(APITestCase):
    """subscriptions_for_document: the kinds that name a document."""

    def setUp(self):
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

    def test_predicate_kinds_do_not_match_a_document(self):
        Subscription.objects.create(user=self.user, kind=Subscription.Kind.NEW_RFC)
        Subscription.objects.create(user=self.user, kind=Subscription.Kind.OBSOLETED)
        self.assertEqual(list(subscriptions_for_document("rfc9110")), [])

    def test_subseries_are_not_expanded_yet(self):
        # BCP 14 is RFC 2119 plus RFC 8174, but Reef has no document metadata,
        # so a change to a constituent does not reach the subseries. See the
        # subseries open item in plan.md.
        DocumentSetEntry.objects.create(document_set=self.set, doc="bcp14")
        Subscription.objects.create(
            user=self.user, kind=Subscription.Kind.SET, document_set=self.set
        )
        self.assertEqual(list(subscriptions_for_document("rfc2119")), [])
