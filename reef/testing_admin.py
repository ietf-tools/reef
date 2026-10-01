# Copyright The IETF Trust 2026, All Rights Reserved
"""Test checks for an admin page built on reef.admin_readonly.ReadOnlyAdminMixin.

The two ways such a page breaks: a field or display method that only fails when the
page renders, and losing the mixin, which turns a table its writer relies on into
one anybody can edit.
"""

from django.urls import reverse


class ReadOnlyAdminChecks:
    """Mixed into a TestCase whose self.client is signed in as a superuser."""

    def _admin_url(self, obj, view, *args):
        meta = obj._meta
        return reverse(f"admin:{meta.app_label}_{meta.model_name}_{view}", args=args)

    def assertAdminRenders(self, obj):
        for url in (
            self._admin_url(obj, "changelist"),
            self._admin_url(obj, "change", obj.pk),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def assertAdminReadOnly(self, obj):
        self.assertEqual(self.client.get(self._admin_url(obj, "add")).status_code, 403)
        self.assertEqual(
            self.client.post(self._admin_url(obj, "change", obj.pk)).status_code, 403
        )
        self.assertEqual(
            self.client.post(self._admin_url(obj, "delete", obj.pk)).status_code, 403
        )
        self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())
