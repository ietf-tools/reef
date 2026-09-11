# Copyright The IETF Trust 2026, All Rights Reserved

import django.db.models.deletion
import simple_history.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subjects', '0004_subject_hierarchy'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricalSubject',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('slug', models.SlugField(help_text='Stable identifier used in URLs and by Red. Changing it leaves the old one behind as an alias, so links naming it still resolve; the name is still the field to edit when only the wording changed.')),
                ('name', models.CharField(db_index=True, help_text='How the subject is shown to readers.', max_length=100)),
                ('description', models.TextField(blank=True, help_text='What belongs under this subject, for whoever curates it next and for a caller drawing a picker.')),
                ('retired_at', models.DateTimeField(blank=True, help_text='When this subject stopped being offered. Clear it to bring the subject back; existing subscriptions keep working either way.', null=True)),
                ('path', models.CharField(db_index=True, editable=False, help_text='Slugs from the top down, separated by a slash. Derived; edit the slug or the parent instead.', max_length=255)),
                ('depth', models.PositiveSmallIntegerField(default=0, editable=False)),
                ('created_at', models.DateTimeField(blank=True, editable=False)),
                ('updated_at', models.DateTimeField(blank=True, editable=False)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('merged_into', models.ForeignKey(blank=True, db_constraint=False, help_text="Set by a merge. The subject this one's documents and followers were moved to.", null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='subjects.subject')),
                ('parent', models.ForeignKey(blank=True, db_constraint=False, help_text='The subject this one sits under. Leave empty for a top-level subject. A document assigned here also counts under every subject above.', null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='subjects.subject')),
            ],
            options={
                'verbose_name': 'historical subject',
                'verbose_name_plural': 'historical subjects',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='HistoricalSubjectAssignment',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('doc', models.CharField(db_index=True, max_length=32)),
                ('assigned_at', models.DateTimeField(blank=True, editable=False)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('subject', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='subjects.subject')),
            ],
            options={
                'verbose_name': 'historical subject assignment',
                'verbose_name_plural': 'historical subject assignments',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]
