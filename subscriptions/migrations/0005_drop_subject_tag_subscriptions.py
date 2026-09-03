# Copyright The IETF Trust 2026, All Rights Reserved
"""Drop the free-text subject_tag subscriptions.

The subject_tag kind held its tag as a string in params, from when a tag was
going to arrive on a change event from the datatracker. Reef now hosts the
vocabulary itself, so a subscription names a Subject row instead.

The old rows cannot be carried across. Resolving one would mean creating a
Subject for whatever string was typed, which is how a curated vocabulary
acquires "secuirty" on its first day, and the strings are unresolvable in
principle: nothing ever validated them, because there was nothing to validate
them against.

Deleting them costs nothing that was working. No subject_tag subscription has
ever produced a notification: the kind was matched on the ingest path, which
is a stub, so every one of these rows is a promise that was never kept.
"""

from django.db import migrations


def drop_subject_tag_subscriptions(apps, schema_editor):
    Subscription = apps.get_model("subscriptions", "Subscription")
    Subscription.objects.filter(kind="subject_tag").delete()


class Migration(migrations.Migration):
    dependencies = [
        (
            "subscriptions",
            "0004_remove_subscription_unique_subscription_per_user_and_more",
        ),
    ]

    operations = [
        # Irreversible in substance rather than in form: reversing restores the
        # column shape, and there is no string left to put back in it.
        migrations.RunPython(drop_subject_tag_subscriptions, migrations.RunPython.noop),
    ]
