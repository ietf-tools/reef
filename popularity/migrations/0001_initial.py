# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="PopularEntry",
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
                (
                    "rank",
                    models.PositiveIntegerField(
                        default=0, help_text="Lower sorts first"
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "popular entries",
                "ordering": ["rank"],
            },
        ),
    ]
