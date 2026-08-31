# Copyright The IETF Trust 2026, All Rights Reserved
"""Give every pending notification a unique fingerprint.

Three steps rather than one, because a unique column cannot be added with a single
default: every existing row would take the same value and collide. Any rows already
queued are real obligations, so they are fingerprinted rather than cleared.
"""

import uuid

from django.db import migrations, models


def fingerprint_existing(apps, schema_editor):
    """Give queued rows a value that is unique but matches nothing.

    Not the real key: these were written before the field existed, so there is no
    occasion to scope them to, and a wrong fingerprint would be worse than an
    arbitrary one. Deduplication starts from the next run.
    """
    PendingNotification = apps.get_model("subscriptions", "PendingNotification")
    for notification in PendingNotification.objects.filter(dedupe_key__isnull=True):
        notification.dedupe_key = uuid.uuid4().hex
        notification.save(update_fields=["dedupe_key"])


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0010_remove_subscription_verified")]

    operations = [
        migrations.AddField(
            model_name="pendingnotification",
            name="dedupe_key",
            field=models.CharField(max_length=64, null=True),
        ),
        migrations.RunPython(fingerprint_existing, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="pendingnotification",
            name="dedupe_key",
            field=models.CharField(max_length=64, unique=True),
        ),
    ]
