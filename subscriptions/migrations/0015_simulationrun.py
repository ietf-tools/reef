# Copyright The IETF Trust 2026, All Rights Reserved

import django.core.serializers.json
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('subscriptions', '0014_webnotification'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SimulationRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'Queued'), ('running', 'Running'), ('succeeded', 'Succeeded'), ('failed', 'Failed')], default='pending', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('upload', models.BinaryField(blank=True, default=b'')),
                ('progress_message', models.CharField(blank=True, max_length=255)),
                ('timings', models.JSONField(blank=True, default=list)),
                ('result', models.JSONField(blank=True, encoder=django.core.serializers.json.DjangoJSONEncoder, null=True)),
                ('error', models.TextField(blank=True)),
                ('triggered_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='simulation_runs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
