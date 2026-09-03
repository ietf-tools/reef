# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0009_alter_subscription_subject"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="subscription",
            name="verified",
        ),
    ]
