# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        (
            "docsets",
            "0006_remove_documentset_unique_document_set_slug_per_owner_and_more",
        ),
    ]

    operations = [
        migrations.RemoveField(
            model_name="documentset",
            name="visibility",
        ),
    ]
