# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0015_simulationrun'),
    ]

    operations = [
        migrations.AddField(
            model_name='simulationrun',
            name='holds',
            field=models.CharField(choices=[('entries', "Entries to add to, or replace in, Red's live index"), ('index', 'A whole rfc-index.json')], default='index', max_length=10),
        ),
    ]
