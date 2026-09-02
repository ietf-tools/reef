# Copyright The IETF Trust 2026, All Rights Reserved
"""The vocabulary as a tree: the derived path, and what the model refuses."""

from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from .models import MAX_DEPTH, Subject


def tree(*paths):
    """Make the subjects named by slash-separated paths, parents first."""
    made = {}
    for path in paths:
        slugs = path.split("/")
        parent = made[slugs[-2]] if len(slugs) > 1 else None
        slug = slugs[-1]
        made[slug] = Subject.objects.create(
            slug=slug, name=slug.replace("-", " ").title(), parent=parent
        )
    return made


class DerivedPathTests(TestCase):
    def test_a_root_path_is_its_slug(self):
        subject = Subject.objects.create(slug="messaging", name="Messaging")
        self.assertEqual(subject.path, "messaging")
        self.assertEqual(subject.depth, 0)

    def test_a_child_path_is_the_parent_path_and_its_slug(self):
        made = tree("messaging", "messaging/email", "messaging/email/smtp")
        self.assertEqual(made["smtp"].path, "messaging/email/smtp")
        self.assertEqual(made["smtp"].depth, 2)

    def test_ancestors_are_read_off_the_path_without_a_query(self):
        made = tree("messaging", "messaging/email", "messaging/email/smtp")
        with self.assertNumQueries(0):
            self.assertEqual(made["smtp"].ancestor_slugs, ["messaging", "email"])

    def test_renaming_a_subject_repaths_everything_beneath_it(self):
        made = tree(
            "messaging",
            "messaging/email",
            "messaging/email/smtp",
            "messaging/email/email-authentication",
            "messaging/email/email-authentication/dkim",
        )
        made["messaging"].slug = "mail"
        made["messaging"].save()

        made["dkim"].refresh_from_db()
        made["smtp"].refresh_from_db()
        self.assertEqual(made["dkim"].path, "mail/email/email-authentication/dkim")
        self.assertEqual(made["smtp"].path, "mail/email/smtp")
        # The rename still leaves the old slug behind as an alias, as it always did.
        self.assertEqual(
            list(made["messaging"].aliases.values_list("slug", flat=True)),
            ["messaging"],
        )

    def test_moving_a_branch_repaths_it_and_corrects_the_depths(self):
        made = tree("messaging", "web", "messaging/email", "messaging/email/smtp")
        made["email"].parent = made["web"]
        made["email"].save()

        made["email"].refresh_from_db()
        made["smtp"].refresh_from_db()
        self.assertEqual(made["email"].path, "web/email")
        self.assertEqual(made["email"].depth, 1)
        self.assertEqual(made["smtp"].path, "web/email/smtp")
        self.assertEqual(made["smtp"].depth, 2)

    def test_a_write_that_cannot_move_a_subject_leaves_the_path_alone(self):
        made = tree("messaging", "messaging/email")
        made["email"].retire()
        made["email"].refresh_from_db()
        self.assertEqual(made["email"].path, "messaging/email")

    def test_two_subjects_cannot_share_a_path(self):
        # Follows from slug uniqueness, and is asserted separately because the
        # published file is keyed on the path.
        Subject.objects.create(slug="email", name="Email")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Subject.objects.create(slug="email", name="Electronic Mail")


class SubtreeQueryTests(TestCase):
    def setUp(self):
        self.made = tree(
            "security",
            "security/cryptography",
            "security/cryptography/sha",
            "security/cryptography-x",
            "web",
        )

    def test_at_or_under_includes_the_subject_itself(self):
        found = Subject.objects.at_or_under(self.made["cryptography"])
        self.assertEqual([s.slug for s in found], ["cryptography", "sha"])

    def test_under_excludes_the_subject_itself(self):
        found = Subject.objects.under(self.made["cryptography"])
        self.assertEqual([s.slug for s in found], ["sha"])

    def test_a_sibling_with_a_longer_slug_is_not_swept_in(self):
        # The reason the separator is appended to the prefix: without it,
        # security/cryptography would match security/cryptography-x.
        found = Subject.objects.at_or_under(self.made["cryptography"])
        self.assertNotIn("cryptography-x", [s.slug for s in found])

    def test_roots_are_the_subjects_with_no_parent(self):
        self.assertEqual(
            sorted(s.slug for s in Subject.objects.roots()), ["security", "web"]
        )

    def test_a_subtree_is_one_query(self):
        with self.assertNumQueries(1):
            list(Subject.objects.at_or_under(self.made["security"]))


class RefusalTests(TestCase):
    def test_a_subject_cannot_be_its_own_parent(self):
        subject = Subject.objects.create(slug="messaging", name="Messaging")
        subject.parent = subject
        with self.assertRaises(ValidationError):
            subject.save()

    def test_a_subject_cannot_be_moved_beneath_itself(self):
        made = tree("messaging", "messaging/email", "messaging/email/smtp")
        made["messaging"].parent = made["smtp"]
        with self.assertRaises(ValidationError):
            made["messaging"].save()

    def test_a_retired_subject_cannot_be_a_parent(self):
        made = tree("messaging")
        made["messaging"].retire()
        with self.assertRaises(ValidationError):
            Subject.objects.create(slug="email", name="Email", parent=made["messaging"])

    def test_the_depth_ceiling_is_enforced(self):
        paths, previous = [], None
        for level in range(MAX_DEPTH):
            previous = f"{previous}/level{level}" if previous else f"level{level}"
            paths.append(previous)
        made = tree(*paths)
        deepest = made[f"level{MAX_DEPTH - 1}"]
        self.assertEqual(deepest.depth, MAX_DEPTH - 1)
        with self.assertRaises(ValidationError):
            Subject.objects.create(slug="toodeep", name="Too Deep", parent=deepest)

    def test_the_ceiling_is_checked_against_the_deepest_descendant(self):
        # A leaf could move here; a branch three deep cannot, and the check has to
        # measure the branch rather than the node being moved.
        made = tree("a", "a/b", "a/b/c", "branch", "branch/x", "branch/x/y")
        made["branch"].parent = made["c"]
        with self.assertRaises(ValidationError):
            made["branch"].save()

    def test_deleting_a_subject_with_children_is_refused(self):
        made = tree("messaging", "messaging/email")
        with self.assertRaises(ProtectedError):
            made["messaging"].delete()
        self.assertTrue(Subject.objects.filter(slug="messaging").exists())

    def test_deleting_a_leaf_still_works(self):
        made = tree("messaging", "messaging/email")
        made["email"].delete()
        self.assertFalse(Subject.objects.filter(slug="email").exists())


class RebuildPathsTests(TestCase):
    def setUp(self):
        self.made = tree("messaging", "messaging/email", "messaging/email/smtp")

    def _corrupt(self):
        # Straight to the database, which is the only way to get a wrong path:
        # save() derives it, so the model cannot be talked into writing one.
        Subject.all_objects.filter(slug="email").update(path="wrong", depth=9)

    def test_check_reports_a_stale_path_without_writing(self):
        self._corrupt()
        out = StringIO()
        call_command("rebuild_paths", "--check", stdout=out)
        self.assertIn("wrong -> messaging/email", out.getvalue())
        self.assertEqual(Subject.all_objects.get(slug="email").path, "wrong")

    def test_rebuilding_restores_every_derived_path(self):
        self._corrupt()
        call_command("rebuild_paths", stdout=StringIO())
        self.assertEqual(Subject.all_objects.get(slug="email").path, "messaging/email")
        self.assertEqual(Subject.all_objects.get(slug="email").depth, 1)

    def test_rebuilding_a_sound_tree_changes_nothing(self):
        out = StringIO()
        call_command("rebuild_paths", stdout=out)
        self.assertIn("Rebuilt 0 of 3", out.getvalue())
