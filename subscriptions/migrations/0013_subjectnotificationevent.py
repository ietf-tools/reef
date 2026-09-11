# Copyright The IETF Trust 2026, All Rights Reserved

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("subscriptions", "0012_historicalsubscription"),
    ]

    operations = [
        migrations.CreateModel(
            name="SubjectNotificationEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("subscription_ids", models.JSONField(default=list)),
                ("event_kind", models.CharField(blank=True, max_length=64, null=True)),
                ("event_key", models.CharField(blank=True, max_length=255, null=True)),
                ("event", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subject_notification_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(
                            ("event_key__isnull", False),
                            ("event_kind__isnull", False),
                        ),
                        fields=("user", "event_kind", "event_key"),
                        name="unique_pending_subject_event",
                    )
                ],
            },
        ),
    ]
