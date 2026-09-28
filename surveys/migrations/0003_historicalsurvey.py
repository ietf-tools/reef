# Copyright The IETF Trust 2026, All Rights Reserved

import django.db.models.deletion
import simple_history.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('surveys', '0002_survey_deleted_at_survey_deleted_reason'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricalSurvey',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('slug', models.SlugField(max_length=100)),
                ('title', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('definition', models.JSONField(default=dict)),
                ('theme', models.JSONField(blank=True, null=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('published', 'Published'), ('closed', 'Closed')], default='draft', max_length=16)),
                ('visibility', models.CharField(choices=[('open', 'Open (anonymous)'), ('authenticated', 'Authenticated only')], default='open', max_length=16)),
                ('audience', models.JSONField(blank=True, null=True)),
                ('deleted_at', models.DateTimeField(blank=True, help_text='Blank for a live survey. Set it to withdraw the survey: it then 404s for the runner and the builder alike, and stops being offered. Clear it to restore. Responses are kept either way.', null=True)),
                ('deleted_reason', models.TextField(blank=True, help_text='Why the survey was withdrawn, for whoever reviews the decision later. Never served through the API.')),
                ('created_at', models.DateTimeField(blank=True, editable=False)),
                ('updated_at', models.DateTimeField(blank=True, editable=False)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('created_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'historical survey',
                'verbose_name_plural': 'historical surveys',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]
