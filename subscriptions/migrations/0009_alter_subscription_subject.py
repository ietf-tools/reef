# Copyright The IETF Trust 2026, All Rights Reserved

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("subjects", "0002_subject_merged_into_subject_retired_at"),
        ("subscriptions", "0008_pendingnotification"),
    ]

    operations = [
        migrations.AlterField(
            model_name="subscription",
            name="subject",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="subscriptions",
                to="subjects.subject",
            ),
        ),
    ]
