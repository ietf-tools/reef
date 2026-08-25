# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from docsets.models import DocumentSet, DocumentSetEntry
from ratings.models import Rating
from subscriptions.models import Subscription

from .api import MAX_BATCH_DOCS

User = get_user_model()

URL = "/api/reef/me/documents/"


class MyDocumentsTests(APITestCase):
    def setUp(self):
        self.reader = User.objects.create(username="reader", oidc_sub="reader")
        self.other = User.objects.create(username="other", oidc_sub="other")
        self.client.force_authenticate(user=self.reader)

    def documents(self, *docs):
        query = "&".join(f"doc={doc}" for doc in docs)
        response = self.client.get(f"{URL}?{query}" if query else URL)
        self.assertEqual(response.status_code, 200, response.content)
        return {row["doc"]: row for row in response.json()["documents"]}

    # --- Access ---------------------------------------------------------

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get(URL).status_code, (401, 403))

    def test_response_is_not_shared_cacheable(self):
        response = self.client.get(URL)
        self.assertIn("Authorization", response.headers["Vary"])
        self.assertIn("Cookie", response.headers["Vary"])
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    # --- Documents ------------------------------------------------------

    def test_named_document_with_no_state_is_still_returned(self):
        self.assertEqual(
            self.documents("rfc9110")["rfc9110"],
            {
                "doc": "rfc9110",
                "your_rating": None,
                "your_subscription_id": None,
                "your_set_ids": [],
            },
        )

    def test_no_documents_named_returns_no_rows(self):
        response = self.client.get(URL)
        self.assertEqual(response.json()["documents"], [])

    def test_rating_subscription_and_sets_in_one_call(self):
        Rating.objects.create(rfc="rfc9110", user=self.reader, value=4)
        subscription = Subscription.objects.create(
            user=self.reader, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        document_set = DocumentSet.objects.create(owner=self.reader, title="Mine")
        DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")

        row = self.documents("rfc9110")["rfc9110"]
        self.assertEqual(row["your_rating"], 4)
        self.assertEqual(row["your_subscription_id"], subscription.id)
        self.assertEqual(row["your_set_ids"], [str(document_set.id)])

    def test_batch_answers_every_named_document(self):
        Rating.objects.create(rfc="rfc9110", user=self.reader, value=5)
        Rating.objects.create(rfc="bcp14", user=self.reader, value=2)
        rows = self.documents("rfc9110", "bcp14", "std66")
        self.assertEqual(rows["rfc9110"]["your_rating"], 5)
        self.assertEqual(rows["bcp14"]["your_rating"], 2)
        self.assertIsNone(rows["std66"]["your_rating"])

    def test_a_document_can_be_in_several_of_the_callers_sets(self):
        first = DocumentSet.objects.create(owner=self.reader, title="One")
        second = DocumentSet.objects.create(owner=self.reader, title="Two")
        for document_set in (first, second):
            DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")
        self.assertEqual(
            sorted(self.documents("rfc9110")["rfc9110"]["your_set_ids"]),
            sorted([str(first.id), str(second.id)]),
        )

    # --- Whose data it is -----------------------------------------------

    def test_another_readers_state_is_not_reported(self):
        Rating.objects.create(rfc="rfc9110", user=self.other, value=1)
        Subscription.objects.create(
            user=self.other, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        theirs = DocumentSet.objects.create(owner=self.other, title="Theirs")
        DocumentSetEntry.objects.create(document_set=theirs, doc="rfc9110")

        row = self.documents("rfc9110")["rfc9110"]
        self.assertIsNone(row["your_rating"])
        self.assertIsNone(row["your_subscription_id"])
        self.assertEqual(row["your_set_ids"], [])

    def test_a_taken_down_set_ticks_nothing_for_its_owner(self):
        document_set = DocumentSet.objects.create(owner=self.reader, title="Mine")
        DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")
        document_set.soft_delete(reason="staff")

        self.assertEqual(self.documents("rfc9110")["rfc9110"]["your_set_ids"], [])
        self.assertEqual(self.client.get(URL).json()["sets"], [])

    def test_a_set_subscription_is_not_a_document_subscription(self):
        """Reaching a document through a subscribed set is not subscribing to
        the document: the box Red draws deletes a subscription by id, and
        there is no rfc-kind subscription here to delete."""
        document_set = DocumentSet.objects.create(owner=self.reader, title="Mine")
        DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")
        Subscription.objects.create(
            user=self.reader,
            kind=Subscription.Kind.SET,
            document_set=document_set,
        )
        self.assertIsNone(self.documents("rfc9110")["rfc9110"]["your_subscription_id"])

    # --- Sets ------------------------------------------------------------

    def test_sets_carry_no_membership(self):
        document_set = DocumentSet.objects.create(owner=self.reader, title="Mine")
        DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")
        [row] = self.client.get(URL).json()["sets"]
        self.assertEqual(row["id"], str(document_set.id))
        self.assertEqual(row["title"], "Mine")
        self.assertNotIn("documents", row)

    def test_another_readers_sets_are_not_listed(self):
        DocumentSet.objects.create(owner=self.other, title="Theirs")
        self.assertEqual(self.client.get(URL).json()["sets"], [])

    # --- Identifiers -----------------------------------------------------

    def test_identifiers_are_canonicalized(self):
        Rating.objects.create(rfc="rfc9110", user=self.reader, value=3)
        rows = self.documents("RFC%209110")
        self.assertEqual(rows["rfc9110"]["your_rating"], 3)

    def test_two_spellings_of_one_document_are_one_row(self):
        rows = self.documents("rfc9110", "RFC%209110")
        self.assertEqual(list(rows), ["rfc9110"])

    def test_a_bare_number_is_ambiguous(self):
        response = self.client.get(f"{URL}?doc=9110")
        self.assertEqual(response.status_code, 400)
        self.assertIn("doc", response.json())

    def test_junk_identifier_is_rejected(self):
        self.assertEqual(self.client.get(f"{URL}?doc=nonsense").status_code, 400)

    def test_over_the_batch_limit_is_rejected(self):
        query = "&".join(f"doc=rfc{n}" for n in range(MAX_BATCH_DOCS + 1))
        response = self.client.get(f"{URL}?{query}")
        self.assertEqual(response.status_code, 400)
        self.assertIn("doc", response.json())

    def test_at_the_batch_limit_is_accepted(self):
        query = "&".join(f"doc=rfc{n}" for n in range(1, MAX_BATCH_DOCS + 1))
        response = self.client.get(f"{URL}?{query}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["documents"]), MAX_BATCH_DOCS)
