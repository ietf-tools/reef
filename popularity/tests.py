# Copyright The IETF Trust 2026, All Rights Reserved
from rest_framework.test import APITestCase

from .models import PopularEntry


class PopularityApiTests(APITestCase):
    def test_public_ordered_list(self):
        PopularEntry.objects.create(rfc="rfc2", rank=2)
        PopularEntry.objects.create(rfc="rfc1", rank=1)
        resp = self.client.get("/api/reef/popularity/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([e["rfc"] for e in resp.json()], ["rfc1", "rfc2"])

    def test_entries_are_stored_canonically(self):
        entry = PopularEntry.objects.create(rfc="RFC 0791", rank=1)
        entry.refresh_from_db()
        self.assertEqual(entry.rfc, "rfc791")
