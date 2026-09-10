# Copyright The IETF Trust 2026, All Rights Reserved

import django.db.models.deletion
import simple_history.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('docsets', '0007_remove_documentset_visibility'),
        ('subjects', '0005_historicalsubject_historicalsubjectassignment'),
        ('subscriptions', '0011_pendingnotification_dedupe_key'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricalSubscription',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('kind', models.CharField(choices=[('new_rfc', 'Any new RFC'), ('by_status', 'New RFC by status'), ('obsoleted', 'RFC obsoleted or made historic'), ('rfc', 'Changes to one specific RFC'), ('set', 'Changes to anything in a document set'), ('subject', 'Changes to anything carrying a subject')], max_length=32)),
                ('params', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(blank=True, editable=False)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('document_set', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='docsets.documentset')),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('subject', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='subjects.subject')),
                ('user', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'historical subscription',
                'verbose_name_plural': 'historical subscriptions',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]
