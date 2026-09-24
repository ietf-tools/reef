# Copyright The IETF Trust 2026, All Rights Reserved
"""Popularity derived from ranking sources, in place of the hand-curated list.

The curated rows are dropped rather than carried over: a rank typed into the
admin has no meaning as a percentile, and the first upload fills both tables.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("popularity", "0002_canonical_doc_ids"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.DeleteModel(name="PopularEntry"),
        migrations.CreateModel(
            name="DocumentPopularity",
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
                ("rfc", models.CharField(max_length=32, unique=True)),
                ("score", models.FloatField()),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField()),
            ],
            options={
                "verbose_name_plural": "document popularities",
                "ordering": ["-score", "rfc"],
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="MatomoRanking",
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
                ("rfc", models.CharField(max_length=32, unique=True)),
                ("score", models.FloatField()),
                ("created_at", models.DateTimeField()),
                ("updated_at", models.DateTimeField()),
            ],
            options={
                "verbose_name_plural": "matomo rankings",
                "ordering": ["-score", "rfc"],
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="MatomoImportRun",
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
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Queued"),
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("rows_seen", models.PositiveIntegerField(default=0)),
                ("rfcs_ranked", models.PositiveIntegerField(default=0)),
                ("rows_ignored", models.PositiveIntegerField(default=0)),
                ("truncated", models.JSONField(blank=True, default=list)),
                ("progress_message", models.CharField(blank=True, max_length=255)),
                ("output", models.TextField(blank=True)),
                ("error", models.TextField(blank=True)),
                (
                    "triggered_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="matomo_import_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
