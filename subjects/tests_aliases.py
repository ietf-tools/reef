# Copyright The IETF Trust 2026, All Rights Reserved
"""Other names for a subject: where they come from, and where they resolve."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APITestCase

from subjects.merge import merge_subjects
from subjects.models import Subject, SubjectAlias, SubjectAssignment


class RenameTests(TestCase):
    """A slug that changes leaves the name it had behind.

    Automatic, because the cost of forgetting falls on a reader following a link that
    was published before anybody thought about renaming.
    """

    def setUp(self):
        self.subject = Subject.objects.create(name="Security", slug="security")

    def test_renaming_a_slug_leaves_an_alias(self):
        self.subject.slug = "security-and-privacy"
        self.subject.save()
        self.assertEqual(
            list(self.subject.aliases.values_list("slug", flat=True)), ["security"]
        )

    def test_renaming_the_name_alone_leaves_nothing(self):
        """Rewording is the ordinary edit, and it does not touch a URL."""
        self.subject.name = "Security and privacy"
        self.subject.save()
        self.assertEqual(self.subject.aliases.count(), 0)

    def test_creating_a_subject_leaves_nothing(self):
        self.assertEqual(SubjectAlias.objects.count(), 0)

    def test_renaming_to_one_of_its_own_aliases_consumes_it(self):
        """Swapping which of two names is canonical, rather than growing a
        duplicate of the one being taken."""
        self.subject.slug = "sec"
        self.subject.save()  # leaves "security"
        self.subject.slug = "security"
        self.subject.save()  # takes it back, and leaves "sec"
        self.assertEqual(
            list(self.subject.aliases.values_list("slug", flat=True)), ["sec"]
        )

    def test_retiring_does_not_look_like_a_rename(self):
        """retire() and unretire() name their fields, so neither pays for a query to
        find out that a slug it never touched has not changed."""
        self.subject.retire()
        self.subject.unretire()
        self.assertEqual(SubjectAlias.objects.count(), 0)

    def test_deleting_a_subject_takes_its_names_with_it(self):
        SubjectAlias.objects.create(slug="sec", subject=self.subject)
        self.subject.delete()
        self.assertEqual(SubjectAlias.objects.count(), 0)


class NameSpaceTests(TestCase):
    """A slug and an alias are one name space, held apart in two tables."""

    def setUp(self):
        self.security = Subject.objects.create(name="Security", slug="security")
        self.routing = Subject.objects.create(name="Routing", slug="routing")

    def test_an_alias_cannot_take_a_slug_a_subject_holds(self):
        with self.assertRaises(ValidationError):
            SubjectAlias.objects.create(slug="routing", subject=self.security)

    def test_a_retired_subject_still_holds_its_slug(self):
        """It resolves to its own redirect, so an alias of that name would sit
        behind it and never be reached."""
        self.routing.retire()
        with self.assertRaises(ValidationError):
            SubjectAlias.objects.create(slug="routing", subject=self.security)

    def test_a_subject_cannot_take_a_slug_that_is_another_subjects_alias(self):
        SubjectAlias.objects.create(slug="sec", subject=self.security)
        self.routing.slug = "sec"
        with self.assertRaises(ValidationError):
            self.routing.full_clean()

    def test_a_subject_may_hold_its_own_alias_while_being_validated(self):
        """full_clean() on an unchanged subject must not trip over its own names."""
        SubjectAlias.objects.create(slug="sec", subject=self.security)
        self.security.full_clean()

    def test_two_subjects_cannot_answer_to_one_name(self):
        SubjectAlias.objects.create(slug="sec", subject=self.security)
        with self.assertRaises(ValidationError):
            SubjectAlias(slug="sec", subject=self.routing).full_clean()


class AliasApiTests(APITestCase):
    def setUp(self):
        self.subject = Subject.objects.create(name="Security", slug="security")
        SubjectAssignment.objects.create(subject=self.subject, doc="rfc9110")
        self.alias = SubjectAlias.objects.create(slug="sec", subject=self.subject)

    def test_an_alias_resolves_to_the_name_it_points_at(self):
        """Enough to redirect, and nothing that could be rendered as a subject."""
        body = self.client.get("/api/reef/subjects/sec/").json()
        self.assertEqual(body, {"slug": "sec", "alias_of": "security"})

    def test_an_alias_carries_no_id_to_subscribe_to(self):
        """It has none. Subscribing names a subject by primary key, which is what
        lets an alias be a name and nothing else."""
        self.assertNotIn("id", self.client.get("/api/reef/subjects/sec/").json())

    def test_the_subject_carries_its_other_names(self):
        body = self.client.get("/api/reef/subjects/security/").json()
        self.assertEqual(body["aliases"], ["sec"])
        self.assertEqual(body["documents"], ["rfc9110"])

    def test_an_alias_is_not_in_the_vocabulary(self):
        listing = self.client.get("/api/reef/subjects/").json()
        self.assertEqual([row["slug"] for row in listing], ["security"])
        self.assertNotIn("aliases", listing[0])

    def test_a_name_that_is_neither_is_still_a_404(self):
        self.assertEqual(self.client.get("/api/reef/subjects/none/").status_code, 404)

    def test_a_subject_wins_a_name_an_alias_shadows(self):
        """bulk_create goes around the validation that refuses this, which is the
        only way to reach the state. It has to be inert rather than ambiguous."""
        SubjectAlias.objects.bulk_create(
            [SubjectAlias(slug="routing", subject=self.subject)]
        )
        routing = Subject.objects.create(name="Routing", slug="routing")
        body = self.client.get("/api/reef/subjects/routing/").json()
        self.assertEqual(body["id"], routing.pk)

    def test_an_alias_of_a_retired_subject_still_points_at_it(self):
        """Two hops to a dead end, which is what a subject retired with nowhere to
        send anybody is. Slow, but not wrong."""
        self.subject.retire()
        self.assertEqual(
            self.client.get("/api/reef/subjects/sec/").json()["alias_of"], "security"
        )
        self.assertIsNone(
            self.client.get("/api/reef/subjects/security/").json()["merged_into"]
        )


class MergedAliasTests(TestCase):
    def setUp(self):
        self.source = Subject.objects.create(name="Security", slug="security")
        self.target = Subject.objects.create(
            name="Security and privacy", slug="security-and-privacy"
        )

    def test_the_targets_names_grow_by_the_sources(self):
        SubjectAlias.objects.create(slug="sec", subject=self.source)
        merge_subjects(self.source, self.target)
        self.assertEqual(
            list(self.target.aliases.values_list("slug", flat=True)), ["sec"]
        )

    def test_a_name_answers_to_one_subject_only(self):
        """Which is why the merge has no collision to decide: the two cannot already
        share a name for it to drop one of."""
        SubjectAlias.objects.create(slug="sec", subject=self.source)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SubjectAlias.objects.create(slug="sec", subject=self.target)

    def test_the_sources_own_slug_does_not_become_an_alias(self):
        """It is still a subject's slug, and the retired row says where it went.
        An alias of that name would be shadowed by the subject holding it."""
        merge_subjects(self.source, self.target)
        self.assertFalse(SubjectAlias.objects.filter(slug="security").exists())
        self.source.refresh_from_db()
        self.assertEqual(self.source.merged_into, self.target)
