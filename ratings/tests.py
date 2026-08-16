# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import Rating

User = get_user_model()


class RatingApiTests(APITestCase):
    def test_anonymous_aggregate_empty(self):
        resp = self.client.get("/api/reef/ratings/rfc9999/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {"rfc": "rfc9999", "average": None, "count": 0, "your_rating": None},
        )

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

    def test_get_returns_the_callers_own_rating(self):
        mine = User.objects.create(username="mine", oidc_sub="mine")
        theirs = User.objects.create(username="theirs", oidc_sub="theirs")
        Rating.objects.create(rfc="rfc9110", user=mine, value=5)
        Rating.objects.create(rfc="rfc9110", user=theirs, value=1)

        self.client.force_authenticate(user=mine)
        body = self.client.get("/api/reef/ratings/rfc9110/").json()
        self.assertEqual(body["your_rating"], 5)
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["average"], 3.0)

    def test_own_rating_is_null_when_unrated_or_anonymous(self):
        rater = User.objects.create(username="rater", oidc_sub="rater")
        Rating.objects.create(rfc="rfc9110", user=rater, value=4)

        # Authenticated, but has not rated this one.
        bystander = User.objects.create(username="by", oidc_sub="by")
        self.client.force_authenticate(user=bystander)
        self.assertIsNone(
            self.client.get("/api/reef/ratings/rfc9110/").json()["your_rating"]
        )

        # Anonymous sees the aggregate and nobody's individual rating.
        self.client.force_authenticate(user=None)
        body = self.client.get("/api/reef/ratings/rfc9110/").json()
        self.assertIsNone(body["your_rating"])
        self.assertEqual(body["average"], 4.0)

    def test_get_varies_on_credentials(self):
        # A shared cache must not hand one user's rating to the next caller.
        resp = self.client.get("/api/reef/ratings/rfc9110/")
        vary = {v.strip().lower() for v in resp.headers.get("Vary", "").split(",")}
        self.assertIn("authorization", vary)
        self.assertIn("cookie", vary)

    def test_put_echoes_the_value_just_set(self):
        user = User.objects.create(username="u4", oidc_sub="s4")
        self.client.force_authenticate(user=user)
        resp = self.client.put("/api/reef/ratings/rfc1/", {"value": 3}, format="json")
        self.assertEqual(resp.json()["your_rating"], 3)

    def test_delete_requires_auth(self):
        resp = self.client.delete("/api/reef/ratings/rfc1/")
        self.assertIn(resp.status_code, (401, 403))

    def test_delete_withdraws_own_rating_only(self):
        mine = User.objects.create(username="mine", oidc_sub="mine")
        theirs = User.objects.create(username="theirs", oidc_sub="theirs")
        Rating.objects.create(rfc="rfc9110", user=mine, value=5)
        Rating.objects.create(rfc="rfc9110", user=theirs, value=1)

        self.client.force_authenticate(user=mine)
        resp = self.client.delete("/api/reef/ratings/rfc9110/")
        self.assertEqual(resp.status_code, 200)
        # The caller's rating is gone and no longer counts towards the average.
        self.assertEqual(
            resp.json(),
            {"rfc": "rfc9110", "average": 1.0, "count": 1, "your_rating": None},
        )
        self.assertFalse(Rating.objects.filter(user=mine).exists())
        self.assertTrue(Rating.objects.filter(user=theirs).exists())

    def test_delete_is_idempotent(self):
        user = User.objects.create(username="u5", oidc_sub="s5")
        self.client.force_authenticate(user=user)
        self.client.put("/api/reef/ratings/rfc1/", {"value": 3}, format="json")
        first = self.client.delete("/api/reef/ratings/rfc1/")
        second = self.client.delete("/api/reef/ratings/rfc1/")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(second.json()["count"], 0)
        self.assertIsNone(second.json()["average"])

    def test_delete_accepts_any_spelling_of_the_identifier(self):
        user = User.objects.create(username="u6", oidc_sub="s6")
        Rating.objects.create(rfc="rfc9110", user=user, value=5)
        self.client.force_authenticate(user=user)
        resp = self.client.delete("/api/reef/ratings/9110/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["rfc"], "rfc9110")
        self.assertEqual(Rating.objects.count(), 0)

    def test_rating_can_be_set_again_after_deletion(self):
        user = User.objects.create(username="u7", oidc_sub="s7")
        self.client.force_authenticate(user=user)
        self.client.put("/api/reef/ratings/rfc1/", {"value": 3}, format="json")
        self.client.delete("/api/reef/ratings/rfc1/")
        resp = self.client.put("/api/reef/ratings/rfc1/", {"value": 5}, format="json")
        self.assertEqual(resp.json()["your_rating"], 5)
        self.assertEqual(Rating.objects.count(), 1)

    def test_delete_rejects_unparseable_identifier(self):
        user = User.objects.create(username="u8", oidc_sub="s8")
        self.client.force_authenticate(user=user)
        resp = self.client.delete("/api/reef/ratings/not-a-document/")
        self.assertEqual(resp.status_code, 400)

    def test_unparseable_identifier_rejected(self):
        resp = self.client.get("/api/reef/ratings/not-a-document/")
        self.assertEqual(resp.status_code, 400)
