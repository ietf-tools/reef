# Copyright The IETF Trust 2026, All Rights Reserved
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from subscriptions.models import Subscription

from .models import Subject, SubjectAssignment

User = get_user_model()


class SubjectModelTests(APITestCase):
    def test_documents_are_stored_canonically(self):
        # The point of canonicalizing here: an assignment has to be joinable to
        # the same document's ratings, sets and subscriptions.
        subject = Subject.objects.create(slug="security", name="Security")
        assignment = SubjectAssignment.objects.create(subject=subject, doc="RFC 9110")
        assignment.refresh_from_db()
        self.assertEqual(assignment.doc, "rfc9110")

    def test_a_bare_number_is_rejected(self):
        # A subject can be assigned to any published series, so "14" does not
        # say which document is meant.
        subject = Subject.objects.create(slug="security", name="Security")
        with self.assertRaises(ValidationError):
            SubjectAssignment.objects.create(subject=subject, doc="14")

    def test_one_document_joins_a_subject_once(self):
        subject = Subject.objects.create(slug="security", name="Security")
        SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SubjectAssignment.objects.create(subject=subject, doc="rfc9110")

    def test_two_spellings_of_one_document_collide(self):
        subject = Subject.objects.create(slug="security", name="Security")
        SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        with self.assertRaises(IntegrityError), transaction.atomic():
            SubjectAssignment.objects.create(subject=subject, doc="RFC 9110")

    def test_a_document_can_carry_several_subjects(self):
        security = Subject.objects.create(slug="security", name="Security")
        routing = Subject.objects.create(slug="routing", name="Routing")
        SubjectAssignment.objects.create(subject=security, doc="rfc9110")
        SubjectAssignment.objects.create(subject=routing, doc="rfc9110")
        self.assertEqual(SubjectAssignment.objects.filter(doc="rfc9110").count(), 2)

    def test_slugs_and_names_are_each_unique(self):
        Subject.objects.create(slug="security", name="Security")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Subject.objects.create(slug="security", name="Other")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Subject.objects.create(slug="other", name="Security")

    def test_deleting_a_subject_takes_its_assignments(self):
        subject = Subject.objects.create(slug="security", name="Security")
        SubjectAssignment.objects.create(subject=subject, doc="rfc9110")
        subject.delete()
        self.assertFalse(SubjectAssignment.objects.exists())


class SubjectApiTests(APITestCase):
    def setUp(self):
        self.security = Subject.objects.create(
            slug="security", name="Security", description="Anything security."
        )
        self.routing = Subject.objects.create(slug="routing", name="Routing")
        SubjectAssignment.objects.create(subject=self.security, doc="rfc9110")

    def test_the_vocabulary_is_public(self):
        # No token: a reader has to be able to see what they would subscribe to
        # before they have signed in.
        response = self.client.get("/api/reef/subjects/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["slug"] for row in response.json()], ["routing", "security"]
        )

    def test_the_list_carries_what_a_picker_needs(self):
        row = self.client.get("/api/reef/subjects/").json()[1]
        self.assertEqual(
            row,
            {
                "id": self.security.pk,
                "slug": "security",
                "name": "Security",
                "description": "Anything security.",
                # parent and path are what a caller builds the tree from; both
                # counts are what it labels a node with without walking one.
                "parent": None,
                "path": "security",
                "document_count": 1,
                "document_count_deep": 1,
            },
        )

    def test_the_list_carries_no_membership(self):
        # Membership would make the payload grow with the catalogue rather than
        # with the vocabulary; it is on the detail read instead.
        self.assertNotIn("documents", self.client.get("/api/reef/subjects/").json()[0])

    def test_filtering_by_document(self):
        response = self.client.get("/api/reef/subjects/?doc=rfc9110")
        self.assertEqual([row["slug"] for row in response.json()], ["security"])

    def test_the_filter_canonicalizes_the_document(self):
        response = self.client.get("/api/reef/subjects/?doc=RFC%209110")
        self.assertEqual([row["slug"] for row in response.json()], ["security"])

    def test_a_document_with_no_subjects_is_an_empty_list(self):
        response = self.client.get("/api/reef/subjects/?doc=rfc8446")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_an_unreadable_document_is_rejected(self):
        response = self.client.get("/api/reef/subjects/?doc=9110")
        self.assertEqual(response.status_code, 400)
        self.assertIn("doc", response.json())

    def test_reading_one_subject_and_its_documents(self):
        response = self.client.get("/api/reef/subjects/security/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["documents"], ["rfc9110"])

    def test_an_unknown_subject_is_a_404(self):
        self.assertEqual(self.client.get("/api/reef/subjects/none/").status_code, 404)

    def test_the_detail_read_carries_the_id_that_subscribing_names(self):
        # Subscribing names the id, and this is the read that supplies one.
        body = self.client.get("/api/reef/subjects/security/").json()
        self.assertEqual(body["id"], self.security.pk)

    def test_there_is_no_write_path(self):
        # Curation is the admin's, so the API is read-only whoever is asking.
        self.assertEqual(
            self.client.post(
                "/api/reef/subjects/", {"slug": "new", "name": "New"}, format="json"
            ).status_code,
            405,
        )


class SubjectAdminTests(APITestCase):
    """The admin is the whole curation UI, so it is worth rendering.

    Nothing else creates a subject or assigns one, and a broken field
    reference here fails at request time rather than at import time.
    """

    def setUp(self):
        self.staff = User.objects.create_superuser(
            username="staff", password="pw", oidc_sub="staff"
        )
        self.client.force_login(self.staff)
        self.security = Subject.objects.create(slug="security", name="Security")
        SubjectAssignment.objects.create(subject=self.security, doc="rfc9110")

    def test_the_pages_render(self):
        for url in [
            "/admin/subjects/subject/",
            f"/admin/subjects/subject/{self.security.pk}/change/",
            "/admin/subjects/subject/add/",
            "/admin/subjects/subjectassignment/",
            "/admin/subjects/subjectassignment/add/",
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_the_listing_counts_documents_without_a_query_per_row(self):
        """The document count is annotated, so the listing does not grow a
        query per subject as the vocabulary does.

        Asserted as "the same number of queries for more rows" rather than as
        an exact count, which would be a test of Django's admin rather than of
        this."""

        def queries_for(count):
            Subject.objects.exclude(pk=self.security.pk).delete()
            for n in range(count - 1):
                Subject.objects.create(slug=f"s{n}", name=f"Subject {n}")
            with CaptureQueriesContext(connection) as captured:
                response = self.client.get("/admin/subjects/subject/")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Documents", response.content.decode())
            return len(captured)

        self.assertEqual(queries_for(2), queries_for(20))

    def test_a_subject_is_created_through_the_admin(self):
        response = self.client.post(
            "/admin/subjects/subject/add/",
            {
                "slug": "routing",
                "name": "Routing",
                "description": "",
                "assignments-TOTAL_FORMS": "0",
                "assignments-INITIAL_FORMS": "0",
                "aliases-TOTAL_FORMS": "0",
                "aliases-INITIAL_FORMS": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Subject.objects.filter(slug="routing").exists())

    def test_a_document_is_assigned_through_the_inline(self):
        response = self.client.post(
            f"/admin/subjects/subject/{self.security.pk}/change/",
            {
                "slug": "security",
                "name": "Security",
                "description": "",
                "assignments-TOTAL_FORMS": "1",
                "assignments-INITIAL_FORMS": "0",
                "assignments-0-doc": "RFC 8446",
                "assignments-0-subject": str(self.security.pk),
                "aliases-TOTAL_FORMS": "0",
                "aliases-INITIAL_FORMS": "0",
            },
        )
        self.assertEqual(response.status_code, 302)
        # Canonicalized on the way in, as everywhere else: the admin is a write
        # path like any other and the constraint compares stored bytes.
        self.assertIn("rfc8446", [a.doc for a in self.security.assignments.all()])

    def test_an_alias_is_added_through_the_inline(self):
        response = self.client.post(
            f"/admin/subjects/subject/{self.security.pk}/change/",
            {
                "slug": "security",
                "name": "Security",
                "description": "",
                "assignments-TOTAL_FORMS": "0",
                "assignments-INITIAL_FORMS": "0",
                "aliases-TOTAL_FORMS": "1",
                "aliases-INITIAL_FORMS": "0",
                "aliases-0-slug": "sec",
                "aliases-0-subject": str(self.security.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(self.security.aliases.values_list("slug", flat=True)), ["sec"]
        )

    def test_searching_by_an_alias_finds_the_subject(self):
        # The question an alias exists to answer: a reader typed this name, which
        # subject is it.
        self.security.aliases.create(slug="sec")
        body = self.client.get("/admin/subjects/subject/?q=sec").content.decode()
        self.assertIn("Security", body)

    def test_searching_by_document_finds_the_subject(self):
        # The reason assignments__doc is in search_fields: the question a
        # curator arrives with is usually about a document, not a subject.
        body = self.client.get("/admin/subjects/subject/?q=rfc9110").content.decode()
        self.assertIn("Security", body)

    def test_a_subscription_shows_which_subject_it_names(self):
        Subscription.objects.create(
            user=self.staff, kind=Subscription.Kind.SUBJECT, subject=self.security
        )
        body = self.client.get("/admin/subscriptions/subscription/").content.decode()
        self.assertIn("Security", body)
