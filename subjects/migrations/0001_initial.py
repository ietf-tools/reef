# Copyright The IETF Trust 2026, All Rights Reserved

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Subject",
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
                    "slug",
                    models.SlugField(
                        help_text="Stable identifier used in URLs and by Red. "
                        "Changing it breaks links that name the old one; the name is "
                        "the field to edit when the wording is what changed.",
                        unique=True,
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="How the subject is shown to readers.",
                        max_length=100,
                        unique=True,
                    ),
                ),
                (
                    "description",
                    models.TextField(
                        blank=True,
                        help_text="What belongs under this subject, for whoever "
                        "curates it next and for a caller drawing a picker.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.CreateModel(
            name="SubjectAssignment",
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
                ("doc", models.CharField(db_index=True, max_length=32)),
                ("assigned_at", models.DateTimeField(auto_now_add=True)),
                (
                    "subject",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assignments",
                        to="subjects.subject",
                    ),
                ),
            ],
            options={
                "ordering": ["doc"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("subject", "doc"), name="unique_document_per_subject"
                    )
                ],
            },
        ),
    ]
