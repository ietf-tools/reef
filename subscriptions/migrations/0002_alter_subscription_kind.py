# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="subscription",
            name="kind",
            field=models.CharField(
                choices=[
                    ("new_rfc", "Any new RFC"),
                    ("by_status", "New RFC by status"),
                    ("obsoleted", "RFC obsoleted or made historic"),
                    ("subject_tag", "RFC with a subject tag"),
                    ("rfc", "Changes to one specific RFC"),
                ],
                max_length=32,
            ),
        ),
    ]
