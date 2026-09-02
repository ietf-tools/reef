# Copyright The IETF Trust 2026, All Rights Reserved
"""The roll-up helper, tested on its own before anything calls it."""

from django.test import TestCase

from .models import Subject, SubjectAssignment
from .tests_hierarchy import tree
from .tree import (
    ancestor_paths,
    covering_subject_ids,
    documents_under,
    rollup,
)

BRANCH = (
    "messaging",
    "messaging/email",
    "messaging/email/smtp",
    "messaging/email/email-authentication",
    "messaging/email/email-authentication/dkim",
    "messaging/mime",
    "security",
    "security/tls",
)


class AncestorPathTests(TestCase):
    def test_a_root_has_no_ancestors(self):
        self.assertEqual(ancestor_paths("messaging"), [])

    def test_ancestors_are_the_prefixes_top_first(self):
        self.assertEqual(
            ancestor_paths("messaging/email/email-authentication/dkim"),
            [
                "messaging",
                "messaging/email",
                "messaging/email/email-authentication",
            ],
        )


class CoveringSubjectTests(TestCase):
    def setUp(self):
        self.made = tree(*BRANCH)
        SubjectAssignment.objects.create(subject=self.made["dkim"], doc="rfc6376")

    def _slugs(self, docs):
        ids = covering_subject_ids(docs)
        return sorted(
            Subject.all_objects.filter(pk__in=ids).values_list("slug", flat=True)
        )

    def test_a_document_is_covered_by_its_subject_and_every_ancestor(self):
        self.assertEqual(
            self._slugs(["rfc6376"]),
            ["dkim", "email", "email-authentication", "messaging"],
        )

    def test_an_unassigned_document_is_covered_by_nothing(self):
        self.assertEqual(self._slugs(["rfc9110"]), [])

    def test_a_retired_subject_still_covers_its_documents(self):
        # Retiring stops a subject being offered; it does not stop the people
        # already following it from matching, which is the point of retiring.
        self.made["dkim"].retire()
        self.assertIn("dkim", self._slugs(["rfc6376"]))

    def test_covering_is_two_queries_at_any_depth(self):
        with self.assertNumQueries(2):
            covering_subject_ids(["rfc6376"])

    def test_a_sibling_branch_is_not_covered(self):
        self.assertNotIn("security", self._slugs(["rfc6376"]))
        self.assertNotIn("mime", self._slugs(["rfc6376"]))


class DocumentsUnderTests(TestCase):
    def setUp(self):
        self.made = tree(*BRANCH)
        SubjectAssignment.objects.create(subject=self.made["dkim"], doc="rfc6376")
        SubjectAssignment.objects.create(subject=self.made["smtp"], doc="rfc5321")
        SubjectAssignment.objects.create(subject=self.made["email"], doc="rfc5322")

    def test_a_leaf_covers_only_its_own(self):
        self.assertEqual(documents_under(self.made["dkim"]), ["rfc6376"])

    def test_a_branch_covers_its_whole_subtree_and_its_own(self):
        self.assertEqual(
            documents_under(self.made["email"]),
            ["rfc5321", "rfc5322", "rfc6376"],
        )

    def test_a_document_in_two_subjects_of_one_branch_counts_once(self):
        SubjectAssignment.objects.create(subject=self.made["email"], doc="rfc6376")
        self.assertEqual(documents_under(self.made["email"]).count("rfc6376"), 1)

    def test_an_empty_branch_covers_what_is_beneath_it(self):
        # The whole reason roll-up exists: messaging has no assignments of its own.
        self.assertEqual(self.made["messaging"].assignments.count(), 0)
        self.assertEqual(
            documents_under(self.made["messaging"]),
            ["rfc5321", "rfc5322", "rfc6376"],
        )


class RollupTests(TestCase):
    def setUp(self):
        self.made = tree(*BRANCH)
        SubjectAssignment.objects.create(subject=self.made["dkim"], doc="rfc6376")
        SubjectAssignment.objects.create(subject=self.made["smtp"], doc="rfc5321")
        SubjectAssignment.objects.create(subject=self.made["email"], doc="rfc5322")
        SubjectAssignment.objects.create(subject=self.made["tls"], doc="rfc8446")

    def test_direct_holds_only_what_was_assigned(self):
        direct, _ = rollup()
        self.assertEqual(direct["messaging"], [])
        self.assertEqual(direct["messaging/email"], ["rfc5322"])

    def test_covered_rolls_up_the_subtree(self):
        _, covered = rollup()
        self.assertEqual(covered["messaging"], ["rfc5321", "rfc5322", "rfc6376"])
        self.assertEqual(covered["security"], ["rfc8446"])

    def test_every_subject_appears_even_with_nothing_under_it(self):
        direct, covered = rollup()
        self.assertEqual(direct["messaging/mime"], [])
        self.assertEqual(covered["messaging/mime"], [])

    def test_documents_are_sorted_by_number_not_lexically(self):
        SubjectAssignment.objects.create(subject=self.made["tls"], doc="rfc10")
        SubjectAssignment.objects.create(subject=self.made["tls"], doc="rfc9")
        _, covered = rollup()
        self.assertEqual(covered["security/tls"], ["rfc9", "rfc10", "rfc8446"])

    def test_the_whole_vocabulary_is_two_queries(self):
        # The trap this module exists to avoid: one subtree query per subject.
        with self.assertNumQueries(2):
            rollup()

    def test_rollup_agrees_with_documents_under(self):
        _, covered = rollup()
        for subject in Subject.all_objects.all():
            self.assertEqual(
                covered[subject.path],
                documents_under(subject),
                subject.path,
            )
