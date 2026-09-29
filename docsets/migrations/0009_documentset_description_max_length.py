# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("docsets", "0008_historicaldocumentset_historicaldocumentsetentry"),
    ]

    operations = [
        migrations.AlterField(
            model_name="documentset",
            name="description",
            field=models.TextField(blank=True, max_length=1000),
        ),
        migrations.AlterField(
            model_name="historicaldocumentset",
            name="description",
            field=models.TextField(blank=True, max_length=1000),
        ),
    ]
