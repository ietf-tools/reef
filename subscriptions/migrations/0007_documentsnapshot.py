# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0006_subscription_subject"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentSnapshot",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1, primary_key=True, serialize=False
                    ),
                ),
                ("payload", models.BinaryField()),
                ("created_on", models.DateField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("id", 1)),
                        name="document_snapshot_singleton",
                    )
                ],
            },
        ),
    ]
