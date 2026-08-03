# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import Subscription

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
