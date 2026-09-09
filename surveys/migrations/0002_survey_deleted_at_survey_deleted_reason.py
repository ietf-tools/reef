# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('surveys', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='survey',
            name='deleted_at',
            field=models.DateTimeField(blank=True, help_text='Blank for a live survey. Set it to withdraw the survey: it then 404s for the runner and the builder alike, and stops being offered. Clear it to restore. Responses are kept either way.', null=True),
        ),
        migrations.AddField(
            model_name='survey',
            name='deleted_reason',
            field=models.TextField(blank=True, help_text='Why the survey was withdrawn, for whoever reviews the decision later. Never served through the API.'),
        ),
    ]
