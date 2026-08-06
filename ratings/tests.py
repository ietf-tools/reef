# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import Rating

User = get_user_model()


class RatingApiTests(APITestCase):
    def test_anonymous_aggregate_empty(self):
        resp = self.client.get("/api/reef/ratings/rfc9999/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"rfc": "rfc9999", "average": None, "count": 0})

    def test_put_requires_auth(self):
        resp = self.client.put("/api/reef/ratings/rfc1/", {"value": 4}, format="json")
        self.assertIn(resp.status_code, (401, 403))

    def test_put_upserts_and_aggregates(self):
        user = User.objects.create(username="u", oidc_sub="s")
        self.client.force_authenticate(user=user)
        r1 = self.client.put("/api/reef/ratings/rfc1/", {"value": 4}, format="json")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()["count"], 1)
        # Same user rates again: update, not duplicate.
        r2 = self.client.put("/api/reef/ratings/rfc1/", {"value": 2}, format="json")
        self.assertEqual(r2.json()["count"], 1)
        self.assertEqual(r2.json()["average"], 2.0)
        self.assertEqual(Rating.objects.count(), 1)

    def test_value_out_of_range_rejected(self):
        user = User.objects.create(username="u2", oidc_sub="s2")
        self.client.force_authenticate(user=user)
        resp = self.client.put("/api/reef/ratings/rfc1/", {"value": 9}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_spellings_of_one_rfc_are_one_rating(self):
        user = User.objects.create(username="u3", oidc_sub="s3")
        self.client.force_authenticate(user=user)
        for path in ("9110", "rfc9110", "RFC%209110", "rfc-09110"):
            with self.subTest(path=path):
                resp = self.client.put(
                    f"/api/reef/ratings/{path}/", {"value": 5}, format="json"
                )
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["rfc"], "rfc9110")
                self.assertEqual(resp.json()["count"], 1)
        self.assertEqual(Rating.objects.count(), 1)
        self.assertEqual(Rating.objects.get().rfc, "rfc9110")

    def test_bare_number_reads_as_an_rfc(self):
        resp = self.client.get("/api/reef/ratings/9110/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["rfc"], "rfc9110")

    def test_unparseable_identifier_rejected(self):
        resp = self.client.get("/api/reef/ratings/not-a-document/")
        self.assertEqual(resp.status_code, 400)
