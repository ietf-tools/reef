# Copyright The IETF Trust 2026, All Rights Reserved
import uuid

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from docsets.models import DocumentSet, DocumentSetEntry
from ratings.models import Rating
from subscriptions.models import Subscription

User = get_user_model()


class DocumentStatsTests(APITestCase):
    def setUp(self):
        self.a = User.objects.create(username="a", oidc_sub="a")
        self.b = User.objects.create(username="b", oidc_sub="b")

    def stats(self, query=""):
        response = self.client.get(f"/api/reef/stats/{query}")
        self.assertEqual(response.status_code, 200)
        return {row["doc"]: row for row in response.json()}

    def test_public(self):
        self.assertEqual(self.client.get("/api/reef/stats/").status_code, 200)

    def test_empty(self):
        self.assertEqual(self.stats(), {})

    def test_rating_aggregate(self):
        Rating.objects.create(rfc="rfc9110", user=self.a, value=5)
        Rating.objects.create(rfc="rfc9110", user=self.b, value=3)
        row = self.stats()["rfc9110"]
        self.assertEqual(row["rating_average"], 4.0)
        self.assertEqual(row["rating_count"], 2)
        self.assertEqual(row["subscriber_count"], 0)
        self.assertEqual(row["set_count"], 0)

    def test_subscriber_count_covers_rfc_and_set_kinds(self):
        document_set = DocumentSet.objects.create(owner=self.b, title="Mine")
        DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")
        Subscription.objects.create(
            user=self.a, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        Subscription.objects.create(
            user=self.b, kind=Subscription.Kind.SET, document_set=document_set
        )
        self.assertEqual(self.stats()["rfc9110"]["subscriber_count"], 2)

    def test_one_user_two_routes_is_one_subscriber(self):
        document_set = DocumentSet.objects.create(owner=self.a, title="Mine")
        DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")
        Subscription.objects.create(
            user=self.a, kind=Subscription.Kind.RFC, params={"rfc": "rfc9110"}
        )
        Subscription.objects.create(
            user=self.a, kind=Subscription.Kind.SET, document_set=document_set
        )
        self.assertEqual(self.stats()["rfc9110"]["subscriber_count"], 1)

    def test_predicate_kinds_are_not_counted(self):
        Rating.objects.create(rfc="rfc9110", user=self.a, value=5)
        Subscription.objects.create(user=self.a, kind=Subscription.Kind.NEW_RFC)
        Subscription.objects.create(user=self.b, kind=Subscription.Kind.OBSOLETED)
        self.assertEqual(self.stats()["rfc9110"]["subscriber_count"], 0)

    def test_set_count_counts_distinct_sets(self):
        for owner, title in ((self.a, "One"), (self.b, "Two")):
            document_set = DocumentSet.objects.create(owner=owner, title=title)
            DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")
        self.assertEqual(self.stats()["rfc9110"]["set_count"], 2)

    def test_set_count_leaves_out_a_set_staff_have_taken_down(self):
        # A deleted set has to read as one that never existed, and the counts
        # are the last place one would otherwise still show up.
        for title, delete in (("Live", False), ("Taken down", True)):
            document_set = DocumentSet.objects.create(owner=self.a, title=title)
            DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")
            if delete:
                document_set.soft_delete("Spam.")
        self.assertEqual(self.stats()["rfc9110"]["set_count"], 1)

    def test_subscriber_count_leaves_out_a_subscription_to_a_deleted_set(self):
        # Really deleting the set would have taken the subscription with it.
        document_set = DocumentSet.objects.create(owner=self.a, title="Taken down")
        DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")
        Subscription.objects.create(
            user=self.b, kind=Subscription.Kind.SET, document_set=document_set
        )
        document_set.soft_delete()
        self.assertEqual(self.stats(), {})

    def test_filter_by_a_deleted_set_is_the_same_404_as_an_unknown_id(self):
        document_set = DocumentSet.objects.create(owner=self.a, title="Mine")
        DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")
        document_set.soft_delete()

        self.client.force_authenticate(user=self.a)  # its owner, no less
        deleted = self.client.get(f"/api/reef/stats/?set={document_set.pk}")
        unknown = self.client.get(f"/api/reef/stats/?set={uuid.uuid4()}")
        self.assertEqual(deleted.status_code, 404)
        self.assertEqual(deleted.json(), unknown.json())

    def test_covers_every_series_a_set_can_hold(self):
        document_set = DocumentSet.objects.create(owner=self.a, title="Mine")
        for doc in ("rfc9110", "bcp14", "std66"):
            DocumentSetEntry.objects.create(document_set=document_set, doc=doc)
        self.assertEqual(sorted(self.stats()), ["bcp14", "rfc9110", "std66"])

    def test_lists_only_documents_with_engagement(self):
        Rating.objects.create(rfc="rfc9110", user=self.a, value=5)
        self.assertEqual(list(self.stats()), ["rfc9110"])

    def test_filter_returns_a_row_even_with_no_engagement(self):
        rows = self.stats("?doc=rfc9110&doc=BCP%2014")
        self.assertEqual(sorted(rows), ["bcp14", "rfc9110"])
        self.assertEqual(
            rows["rfc9110"],
            {
                "doc": "rfc9110",
                "rating_average": None,
                "rating_count": 0,
                "subscriber_count": 0,
                "set_count": 0,
            },
        )

    def test_filter_rejects_an_ambiguous_identifier(self):
        # Rows can be about any series, so a bare number has no reading here.
        self.assertEqual(self.client.get("/api/reef/stats/?doc=9110").status_code, 400)

    def test_filter_by_set_returns_a_row_per_member(self):
        document_set = DocumentSet.objects.create(owner=self.a, title="Mine")
        for doc in ("rfc9110", "bcp14"):
            DocumentSetEntry.objects.create(document_set=document_set, doc=doc)
        Rating.objects.create(rfc="rfc9110", user=self.b, value=4)
        Rating.objects.create(rfc="rfc791", user=self.b, value=1)  # not a member

        rows = self.stats(f"?set={document_set.pk}")
        self.assertEqual(sorted(rows), ["bcp14", "rfc9110"])
        self.assertEqual(rows["rfc9110"]["rating_average"], 4.0)
        self.assertEqual(rows["bcp14"]["rating_count"], 0)  # member, no engagement

    def test_filter_by_set_and_doc_intersects(self):
        document_set = DocumentSet.objects.create(owner=self.a, title="Mine")
        for doc in ("rfc9110", "bcp14"):
            DocumentSetEntry.objects.create(document_set=document_set, doc=doc)

        rows = self.stats(f"?set={document_set.pk}&doc=bcp14&doc=std66")
        self.assertEqual(list(rows), ["bcp14"])  # std66 is not in the set

    def test_an_id_that_names_no_set_is_a_404(self):
        self.assertEqual(
            self.client.get(f"/api/reef/stats/?set={uuid.uuid4()}").status_code, 404
        )

    def test_the_filter_answers_the_same_whoever_asks(self):
        # Holding the set's id is the whole of the permission here, as it is on
        # the set read: there is no visibility left to condition on.
        document_set = DocumentSet.objects.create(owner=self.a, title="Mine")
        DocumentSetEntry.objects.create(document_set=document_set, doc="rfc9110")

        for user in (None, self.a, self.b):
            with self.subTest(user=user):
                self.client.force_authenticate(user=user)
                self.assertEqual(
                    list(self.stats(f"?set={document_set.pk}")), ["rfc9110"]
                )

    def test_unknown_set_is_not_found(self):
        self.assertEqual(
            self.client.get(f"/api/reef/stats/?set={uuid.uuid4()}").status_code, 404
        )

    def test_set_filter_rejects_an_id_that_is_not_a_uuid(self):
        for raw in ("abc", "9999", ""):
            with self.subTest(set=raw):
                self.assertEqual(
                    self.client.get(f"/api/reef/stats/?set={raw}").status_code, 400
                )

    def test_empty_set_returns_no_rows(self):
        document_set = DocumentSet.objects.create(owner=self.a, title="Mine")
        Rating.objects.create(rfc="rfc9110", user=self.b, value=4)
        self.assertEqual(self.stats(f"?set={document_set.pk}"), {})

    def test_rows_are_ordered(self):
        for doc in ("std66", "rfc9110", "bcp14"):
            Subscription.objects.create(
                user=self.a, kind=Subscription.Kind.RFC, params={"rfc": doc}
            )
        response = self.client.get("/api/reef/stats/")
        self.assertEqual(
            [row["doc"] for row in response.json()], ["bcp14", "rfc9110", "std66"]
        )
