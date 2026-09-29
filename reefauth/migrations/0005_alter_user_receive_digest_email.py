# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reefauth", "0004_user_receive_digest_email"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="receive_digest_email",
            field=models.BooleanField(default=False),
        ),
    ]
