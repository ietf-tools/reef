# Copyright The IETF Trust 2026, All Rights Reserved
import zlib

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from reef.testing_admin import ReadOnlyAdminChecks
from reefauth.testing import login

from .changes import save_snapshot
from .models import (
    DocumentSnapshot,
    PendingNotification,
    SubjectNotificationEvent,
)

User = get_user_model()


class NotificationPipelineAdminTests(ReadOnlyAdminChecks, TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(
            username="staff", oidc_sub="staff", password="x"
        )
        login(self.client, self.staff)
        save_snapshot({"rfc9110": {}, "rfc9111": {}}, None)
        SubjectNotificationEvent.objects.create(
            user=self.staff, event_kind="rfc_change", event_key="k", event={}
        )
        PendingNotification.objects.create(
            user=self.staff, dedupe_key="d", events=[{"doc": "rfc9110"}]
        )

    def test_changelists_and_detail_pages_render(self):
        for model in (DocumentSnapshot, SubjectNotificationEvent, PendingNotification):
            self.assertAdminRenders(model.objects.get())

    def test_snapshot_shows_its_document_count(self):
        url = reverse("admin:subscriptions_documentsnapshot_changelist")
        self.assertContains(
            self.client.get(url), '<td class="field-document_count">2</td>', html=True
        )

    def test_snapshot_says_when_it_cannot_be_read(self):
        DocumentSnapshot.objects.update(payload=zlib.compress(b"not json"))
        url = reverse("admin:subscriptions_documentsnapshot_changelist")
        self.assertContains(self.client.get(url), "unreadable")

    def test_rows_cannot_be_added_changed_or_deleted(self):
        for model in (DocumentSnapshot, SubjectNotificationEvent, PendingNotification):
            self.assertAdminReadOnly(model.objects.get())
