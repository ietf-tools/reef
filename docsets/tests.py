# Copyright The IETF Trust 2026, All Rights Reserved
import uuid

from django.contrib.admin.sites import site
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
        self.assertNotIn("visibility", body)  # not part of the API
        self.assertNotIn("slug", body)  # the id is the whole of a set's identity
        self.assertEqual(body["documents"], [])
        self.assertEqual(body["owner_name"], "A Person")

    def test_id_is_a_random_uuid(self):
        # Sets are public by default and their URLs are handed around, so the
        # id has to be unguessable: a sequential one would let anyone walk the
        # range and read every set in the system.
        ids = [
            uuid.UUID(self.create_set(title=f"Set {n}").json()["id"]) for n in range(3)
        ]
        self.assertEqual(len({str(i) for i in ids}), 3)
        self.assertTrue(all(i.version == 4 for i in ids))

    def test_visibility_cannot_be_set_through_the_api(self):
        # Not a 400: visibility is not a field here, so DRF ignores it like any
        # other unknown key. What matters is that the set is still public and
        # that no client can talk itself into a private one.
        response = self.create_set(visibility="private")
        self.assertEqual(response.status_code, 201)
        self.assertNotIn("visibility", response.json())
        self.assertEqual(
            DocumentSet.objects.get(pk=response.json()["id"]).visibility,
            DocumentSet.Visibility.PUBLIC,
        )

    def test_an_update_cannot_republish_a_set_staff_have_unpublished(self):
        # A set the admin has taken down keeps its visibility through anything
        # its owner does: a retitle is not a decision to publish it again, and
        # the API offers no way to ask for one.
        private = DocumentSet.objects.create(
            owner=self.user,
            title="Older",
            visibility=DocumentSet.Visibility.PRIVATE,
        )
        for method, payload in (
            (self.client.put, {"title": "Renamed", "visibility": "public"}),
            (self.client.patch, {"visibility": "public"}),
        ):
            with self.subTest(method=method.__name__):
                response = method(
                    f"/api/reef/sets/{private.pk}/", payload, format="json"
                )
                self.assertEqual(response.status_code, 200)
                private.refresh_from_db()
                self.assertEqual(private.visibility, DocumentSet.Visibility.PRIVATE)

    def test_retitling_leaves_the_url_alone(self):
        # The id is a set's identity, so a link that was shared before the
        # retitle is the same link afterwards.
        set_id = self.create_set().json()["id"]
        response = self.client.patch(
            f"/api/reef/sets/{set_id}/", {"title": "HTTP semantics"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "HTTP semantics")
        self.assertEqual(response.json()["id"], set_id)

    def test_one_owner_can_have_two_sets_with_the_same_title(self):
        # Nothing has to tell them apart: two sets with one title are two ids.
        first, second = self.create_set(), self.create_set()
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.json()["id"], second.json()["id"])

    def test_a_title_nothing_can_be_made_of_is_still_a_title(self):
        self.assertEqual(self.create_set(title="!!!").status_code, 201)

    def test_sets_are_scoped_to_their_owner(self):
        # The listing and every write are the owner's; reading a public set is
        # not, because that is the shared link. Their private set is a 404, and
        # so is a write to either, whether it is public or not.
        other = User.objects.create(username="o", oidc_sub="s2")
        theirs = DocumentSet.objects.create(owner=other, title="Theirs")
        theirs_private = DocumentSet.objects.create(
            owner=other, title="Also theirs", visibility=DocumentSet.Visibility.PRIVATE
        )

        self.create_set()
        listing = self.client.get("/api/reef/sets/").json()
        self.assertEqual([row["title"] for row in listing], ["HTTP core"])
        self.assertEqual(
            self.client.get(f"/api/reef/sets/{theirs.pk}/").status_code, 200
        )
        self.assertEqual(
            self.client.get(f"/api/reef/sets/{theirs_private.pk}/").status_code, 404
        )
        for pk in (theirs.pk, theirs_private.pk):
            with self.subTest(set=pk):
                self.assertEqual(
                    self.client.delete(f"/api/reef/sets/{pk}/").status_code, 404
                )
                self.assertEqual(
                    self.client.put(
                        f"/api/reef/sets/{pk}/documents/rfc9110/"
                    ).status_code,
                    404,
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
    """The shared-link read: one URL per set, no token needed for a public one."""

    def setUp(self):
        self.owner = User.objects.create(username="u", oidc_sub="s", name="A Person")
        self.stranger = User.objects.create(username="o", oidc_sub="s2")
        self.document_set = DocumentSet.objects.create(
            owner=self.owner, title="HTTP core", description="Public list."
        )
        DocumentSetEntry.objects.create(document_set=self.document_set, doc="rfc9110")

    def url(self):
        return f"/api/reef/sets/{self.document_set.pk}/"

    def unpublish(self):
        self.document_set.visibility = DocumentSet.Visibility.PRIVATE
        self.document_set.save()

    def test_public_set_reads_anonymously(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["title"], "HTTP core")
        self.assertEqual(body["owner_name"], "A Person")
        self.assertEqual([e["doc"] for e in body["documents"]], ["rfc9110"])

    def test_private_set_is_not_found_rather_than_forbidden(self):
        self.unpublish()
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.client.force_authenticate(user=self.stranger)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_the_owner_still_reads_their_own_unpublished_set(self):
        self.unpublish()
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.get(self.url()).status_code, 200)

    def test_unpublishing_stops_public_reads(self):
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.unpublish()
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_reading_a_set_is_not_permission_to_change_it(self):
        # The same URL serves the read and the writes, so the writes have to
        # scope themselves: a stranger gets the 404 they would get for a set
        # that does not exist, and an anonymous caller is refused outright.
        self.client.force_authenticate(user=self.stranger)
        for method, kwargs in (
            (self.client.patch, {"data": {"title": "Theirs now"}, "format": "json"}),
            (self.client.put, {"data": {"title": "Theirs now"}, "format": "json"}),
            (self.client.delete, {}),
        ):
            with self.subTest(method=method.__name__):
                self.assertEqual(method(self.url(), **kwargs).status_code, 404)

        self.client.force_authenticate(user=None)
        self.assertIn(self.client.delete(self.url()).status_code, (401, 403))
        self.assertTrue(DocumentSet.objects.filter(pk=self.document_set.pk).exists())


class SoftDeletedDocumentSetTests(APITestCase):
    """A set staff have taken down has to read as one that never existed.

    Which is why every assertion here compares the taken-down set against a
    freshly made-up id rather than just against a status code: the same status
    with a different body would still tell a caller that a set is there.
    """

    def setUp(self):
        self.owner = User.objects.create(username="u", oidc_sub="s", name="A Person")
        self.document_set = DocumentSet.objects.create(
            owner=self.owner, title="HTTP core", description="Public list."
        )
        DocumentSetEntry.objects.create(document_set=self.document_set, doc="rfc9110")
        self.document_set.soft_delete("Advertising in the description.")
        self.client.force_authenticate(user=self.owner)

    def paths(self, set_id):
        return {
            "detail": f"/api/reef/sets/{set_id}/",
            "document": f"/api/reef/sets/{set_id}/documents/rfc9110/",
            "order": f"/api/reef/sets/{set_id}/order/",
        }

    def assert_answers_alike(self, request):
        """The same answer for the deleted set as for an id nobody ever had."""
        deleted = request(self.paths(self.document_set.pk))
        unknown = request(self.paths(uuid.uuid4()))
        self.assertEqual(deleted.status_code, 404)
        self.assertEqual(deleted.status_code, unknown.status_code)
        self.assertEqual(deleted.json(), unknown.json())

    def test_the_owner_cannot_read_or_change_it(self):
        for name, request in (
            ("get", lambda p: self.client.get(p["detail"])),
            (
                "patch",
                lambda p: self.client.patch(
                    p["detail"], {"title": "Renamed"}, format="json"
                ),
            ),
            (
                "put",
                lambda p: self.client.put(
                    p["detail"], {"title": "Renamed"}, format="json"
                ),
            ),
            ("delete", lambda p: self.client.delete(p["detail"])),
            ("add document", lambda p: self.client.put(p["document"])),
            ("remove document", lambda p: self.client.delete(p["document"])),
            (
                "reorder",
                lambda p: self.client.put(
                    p["order"], {"documents": ["rfc9110"]}, format="json"
                ),
            ),
        ):
            with self.subTest(request=name):
                self.assert_answers_alike(request)

    def test_it_is_gone_from_the_owners_listing(self):
        self.assertEqual(self.client.get("/api/reef/sets/").json(), [])

    def test_the_anonymous_read_does_not_confirm_that_it_exists(self):
        self.client.force_authenticate(user=None)
        self.assert_answers_alike(lambda p: self.client.get(p["detail"]))

    def test_only_a_caller_that_asks_for_deleted_sets_sees_it(self):
        self.assertEqual(DocumentSet.objects.count(), 0)
        self.assertEqual(self.owner.document_sets.count(), 0)  # reverse relation too
        self.assertEqual(DocumentSet.all_objects.count(), 1)
        self.assertEqual(DocumentSet.all_objects.deleted().count(), 1)
        self.assertEqual(DocumentSet.all_objects.live().count(), 0)

    def test_the_admin_can_still_find_it(self):
        # The takedown has to be reviewable and reversible, and the admin is
        # the only place either can happen.
        listing = site._registry[DocumentSet].get_queryset(None)
        self.assertEqual([s.pk for s in listing], [self.document_set.pk])

    def test_the_takedown_keeps_the_set_and_its_reason(self):
        stored = DocumentSet.all_objects.get(pk=self.document_set.pk)
        self.assertTrue(stored.is_deleted)
        self.assertEqual(stored.deleted_reason, "Advertising in the description.")
        self.assertEqual([e.doc for e in stored.entries.all()], ["rfc9110"])

    def test_a_reason_is_optional(self):
        other = DocumentSet.objects.create(owner=self.owner, title="Another")
        other.soft_delete()
        self.assertTrue(DocumentSet.all_objects.get(pk=other.pk).is_deleted)
        self.assertEqual(other.deleted_reason, "")

    def test_restoring_puts_the_set_and_its_documents_back(self):
        self.document_set.restore()
        self.assertFalse(
            DocumentSet.all_objects.get(pk=self.document_set.pk).is_deleted
        )
        self.assertEqual(self.document_set.deleted_reason, "")

        response = self.client.get(f"/api/reef/sets/{self.document_set.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([e["doc"] for e in response.json()["documents"]], ["rfc9110"])

    def test_the_api_never_carries_the_takedown_fields(self):
        # Not part of the contract: a set body that carried deleted_at would
        # publish a moderation decision, and there is no live set it could be
        # true of anyway.
        body = self.client.post(
            "/api/reef/sets/", {"title": "Live one"}, format="json"
        ).json()
        self.assertNotIn("deleted_at", body)
        self.assertNotIn("deleted_reason", body)

    def test_the_title_of_a_deleted_set_can_be_used_again(self):
        # The row is still there, so a new set with the same title has to be
        # allowed rather than colliding with a set nobody can see.
        response = self.client.post(
            "/api/reef/sets/", {"title": "HTTP core"}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(response.json()["id"], str(self.document_set.pk))
