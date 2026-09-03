# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("docsets", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="documentset",
            name="visibility",
            field=models.CharField(
                choices=[("private", "Private"), ("public", "Public")],
                default="public",
                help_text="Sets are public: a title and its membership are readable "
                "by anyone with the link. Private is kept for staff to unpublish one.",
                max_length=16,
            ),
        ),
    ]
