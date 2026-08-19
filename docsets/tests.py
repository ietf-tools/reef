# Copyright The IETF Trust 2026, All Rights Reserved
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework.test import APITestCase

from .models import DocumentSet, DocumentSetEntry

User = get_user_model()


class DocumentSetApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username="u", oidc_sub="s", name="A Person")
        self.client.force_authenticate(user=self.user)

    def create_set(self, title="HTTP core", **kwargs):
        return self.client.post(
            "/api/reef/sets/", {"title": title, **kwargs}, format="json"
        )

    def test_requires_auth(self):
        self.client.force_authenticate(user=None)
        self.assertIn(self.client.get("/api/reef/sets/").status_code, (401, 403))

    def test_create_with_title_and_description(self):
        response = self.create_set(description="The documents I am reviewing.")
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["title"], "HTTP core")
        self.assertEqual(body["description"], "The documents I am reviewing.")
        self.assertEqual(body["slug"], "http-core")
        self.assertEqual(body["visibility"], "public")  # the only value on offer
        self.assertEqual(body["documents"], [])
        self.assertEqual(body["owner_name"], "A Person")

    def test_id_is_a_random_uuid(self):
        # Sets are public by default and their URLs are handed around, so the
        # id has to be unguessable: a sequential one would let anyone walk the
        # range and read every set in the system.
        ids = [uuid.UUID(self.create_set(title=f"Set {n}").json()["id"]) for n in range(3)]
        self.assertEqual(len({str(i) for i in ids}), 3)
        self.assertTrue(all(i.version == 4 for i in ids))

    def test_create_accepts_an_explicit_public(self):
        response = self.create_set(visibility="public")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["visibility"], "public")

    def test_create_refuses_private(self):
        response = self.create_set(visibility="private")
        self.assertEqual(response.status_code, 400)
        self.assertIn("visibility", response.json())
        self.assertFalse(DocumentSet.objects.exists())

    def test_update_refuses_private(self):
        set_id = self.create_set().json()["id"]
        for method in (self.client.patch, self.client.put):
            with self.subTest(method=method.__name__):
                response = method(
                    f"/api/reef/sets/{set_id}/",
                    {"title": "HTTP core", "visibility": "private"},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(
            DocumentSet.objects.get(pk=set_id).visibility,
            DocumentSet.Visibility.PUBLIC,
        )

    def test_a_put_that_omits_visibility_does_not_publish_a_private_set(self):
        # A set the admin has unpublished keeps its visibility: a retitle by its
        # owner is not a decision to publish it again.
        private = DocumentSet.objects.create(
            owner=self.user,
            title="Older",
            visibility=DocumentSet.Visibility.PRIVATE,
        )
        response = self.client.put(
            f"/api/reef/sets/{private.pk}/", {"title": "Renamed"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        private.refresh_from_db()
        self.assertEqual(private.visibility, DocumentSet.Visibility.PRIVATE)

    def test_retitle_moves_the_slug(self):
        set_id = self.create_set().json()["id"]
        response = self.client.patch(
            f"/api/reef/sets/{set_id}/", {"title": "HTTP semantics"}, format="json"
        )
        self.assertEqual(response.json()["slug"], "http-semantics")

    def test_slug_is_unique_per_owner(self):
        first = self.create_set().json()
        second = self.create_set().json()
        self.assertEqual(first["slug"], "http-core")
        self.assertEqual(second["slug"], "http-core-2")

    def test_untitleable_title_still_gets_a_slug(self):
        self.assertEqual(self.create_set(title="!!!").json()["slug"], "set")

    def test_sets_are_scoped_to_their_owner(self):
        other = User.objects.create(username="o", oidc_sub="s2")
        theirs = DocumentSet.objects.create(owner=other, title="Theirs")

        self.create_set()
        listing = self.client.get("/api/reef/sets/").json()
        self.assertEqual(len(listing), 1)
        self.assertEqual(
            self.client.get(f"/api/reef/sets/{theirs.pk}/").status_code, 404
        )
        self.assertEqual(
            self.client.delete(f"/api/reef/sets/{theirs.pk}/").status_code, 404
        )


class DocumentSetMembershipTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create(username="u", oidc_sub="s")
        self.client.force_authenticate(user=self.user)
        self.set_id = self.client.post(
            "/api/reef/sets/", {"title": "Mine"}, format="json"
        ).json()["id"]

    def add(self, doc):
        return self.client.put(f"/api/reef/sets/{self.set_id}/documents/{doc}/")

    def docs(self):
        body = self.client.get(f"/api/reef/sets/{self.set_id}/").json()
        return [entry["doc"] for entry in body["documents"]]

    def test_add_rfcs_and_subseries(self):
        self.assertEqual(self.add("rfc9110").status_code, 201)
        self.assertEqual(self.add("bcp14").status_code, 201)
        self.assertEqual(self.add("std66").status_code, 201)
        self.assertEqual(self.docs(), ["rfc9110", "bcp14", "std66"])

    def test_adding_is_idempotent_across_spellings(self):
        self.assertEqual(self.add("rfc9110").status_code, 201)
        for spelling in ("rfc9110", "RFC%209110", "rfc-09110"):
            with self.subTest(spelling=spelling):
                self.assertEqual(self.add(spelling).status_code, 200)  # not created
        self.assertEqual(self.docs(), ["rfc9110"])

    def test_a_bare_number_is_ambiguous_in_a_set(self):
        # A set holds any series, so 14 could be rfc14 or bcp14.
        self.assertEqual(self.add("14").status_code, 400)
        self.assertEqual(self.docs(), [])

    def test_remove_is_idempotent_and_canonicalizing(self):
        self.add("rfc9110")
        self.assertEqual(
            self.client.delete(
                f"/api/reef/sets/{self.set_id}/documents/RFC%209110/"
            ).status_code,
            204,
        )
        self.assertEqual(self.docs(), [])
        self.assertEqual(
            self.client.delete(
                f"/api/reef/sets/{self.set_id}/documents/rfc9110/"
            ).status_code,
            204,  # already gone
        )

    def test_reorder(self):
        for doc in ("rfc9110", "bcp14", "std66"):
            self.add(doc)
        response = self.client.put(
            f"/api/reef/sets/{self.set_id}/order/",
            {"documents": ["std66", "RFC 9110", "bcp14"]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.docs(), ["std66", "rfc9110", "bcp14"])

    def test_reorder_must_list_exactly_the_membership(self):
        self.add("rfc9110")
        self.add("bcp14")
        for documents in (
            ["rfc9110"],  # short
            ["rfc9110", "bcp14", "std66"],  # adds
            ["rfc9110", "rfc9110"],  # repeats
            ["rfc9110", "not-a-document"],
        ):
            with self.subTest(documents=documents):
                response = self.client.put(
                    f"/api/reef/sets/{self.set_id}/order/",
                    {"documents": documents},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.docs(), ["rfc9110", "bcp14"])

    def test_membership_is_scoped_to_the_owner(self):
        other = User.objects.create(username="o", oidc_sub="s2")
        theirs = DocumentSet.objects.create(owner=other, title="Theirs")
        self.assertEqual(
            self.client.put(
                f"/api/reef/sets/{theirs.pk}/documents/rfc9110/"
            ).status_code,
            404,
        )

    def test_model_rejects_an_unparseable_document(self):
        with self.assertRaises(ValidationError):
            DocumentSetEntry.objects.create(
                document_set=DocumentSet.objects.get(pk=self.set_id), doc="9110"
            )


class PublicDocumentSetTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create(username="u", oidc_sub="s", name="A Person")
        self.document_set = DocumentSet.objects.create(
            owner=self.owner, title="HTTP core", description="Public list."
        )
        DocumentSetEntry.objects.create(document_set=self.document_set, doc="rfc9110")

    def url(self, slug=None):
        slug = slug or self.document_set.slug
        return f"/api/reef/sets/{self.document_set.pk}/{slug}/"

    def unpublish(self):
        self.document_set.visibility = DocumentSet.Visibility.PRIVATE
        self.document_set.save()

    def test_private_set_is_not_found_rather_than_forbidden(self):
        self.unpublish()
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 404)

    def test_public_set_reads_anonymously(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["title"], "HTTP core")
        self.assertEqual(body["owner_name"], "A Person")
        self.assertEqual([e["doc"] for e in body["documents"]], ["rfc9110"])

    def test_stale_slug_redirects_to_the_current_url(self):
        response = self.client.get(self.url(slug="old-title"))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], self.url())

    def test_unpublishing_stops_public_reads(self):
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.unpublish()
        self.assertEqual(self.client.get(self.url()).status_code, 404)
