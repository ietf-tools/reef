# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("docsets", "0005_documentset_deleted_at_documentset_deleted_reason"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="documentset",
            name="unique_document_set_slug_per_owner",
        ),
        migrations.RemoveField(
            model_name="documentset",
            name="slug",
        ),
    ]
