# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("precomputer", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PendingDocumentChange",
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
                ("doc", models.CharField(max_length=32, unique=True)),
                ("first_seen", models.DateTimeField(auto_now_add=True)),
                ("last_seen", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["first_seen"],
            },
        ),
    ]
