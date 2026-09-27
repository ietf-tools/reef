# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reefauth", "0003_alter_user_avatar"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="receive_digest_email",
            field=models.BooleanField(default=True),
        ),
    ]
