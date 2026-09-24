# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subjects', '0006_subjectsyncrun'),
    ]

    operations = [
        migrations.AddField(
            model_name='historicalsubject',
            name='upstream_uuid',
            field=models.UUIDField(blank=True, db_index=True, editable=False, help_text='The tag this subject mirrors in rfc-subject-tags. Set by the sync.', null=True),
        ),
        migrations.AddField(
            model_name='subject',
            name='upstream_uuid',
            field=models.UUIDField(blank=True, editable=False, help_text='The tag this subject mirrors in rfc-subject-tags. Set by the sync.', null=True, unique=True),
        ),
    ]
