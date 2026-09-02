# Copyright The IETF Trust 2026, All Rights Reserved
"""Make the vocabulary a tree.

Additive and reversible. Every subject that exists becomes a root, so nothing
about what is assigned, offered or subscribed to changes when this is applied:
a root's path is its slug and its depth is zero, which is exactly the shape the
flat vocabulary already had without saying so.

The path column arrives in three steps rather than one because it is derived and
unique. It cannot be added non-null with no default, and it cannot be added
unique with one, so it comes in blank, is filled from the slug, and only then
acquires the constraint.
"""

import django.db.models.deletion
from django.db import migrations, models


def populate_paths(apps, schema_editor):
    """Every existing subject is a root, so its path is its slug."""
    Subject = apps.get_model("subjects", "Subject")
    rows = list(Subject.objects.all())
    for subject in rows:
        subject.path = subject.slug
        subject.depth = 0
    if rows:
        Subject.objects.bulk_update(rows, ["path", "depth"])


def clear_paths(apps, schema_editor):
    """Reversing leaves the column about to be dropped, so blanking it is enough."""
    Subject = apps.get_model("subjects", "Subject")
    Subject.objects.update(path="", depth=0)


class Migration(migrations.Migration):
    dependencies = [
        ("subjects", "0003_alter_subject_slug_subjectalias"),
    ]

    operations = [
        migrations.AddField(
            model_name="subject",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                help_text="The subject this one sits under. Leave empty for a "
                "top-level subject. A document assigned here also counts under "
                "every subject above.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="children",
                to="subjects.subject",
            ),
        ),
        migrations.AddField(
            model_name="subject",
            name="depth",
            field=models.PositiveSmallIntegerField(default=0, editable=False),
        ),
        migrations.AddField(
            model_name="subject",
            name="path",
            field=models.CharField(default="", editable=False, max_length=255),
            preserve_default=False,
        ),
        migrations.RunPython(populate_paths, clear_paths),
        migrations.AlterField(
            model_name="subject",
            name="path",
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text="Slugs from the top down, separated by a slash. Derived; "
                "edit the slug or the parent instead.",
                max_length=255,
                unique=True,
            ),
        ),
    ]
