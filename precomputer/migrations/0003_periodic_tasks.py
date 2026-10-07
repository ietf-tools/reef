# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations

# Initial schedules, in UTC. Existing rows are not modified, so admin edits are kept.
PERIODIC_TASKS = {
    # Rebuilds and purges every file, as a floor for the targeted runs.
    "precompute-all": (
        "precomputer.tasks.precompute_all",
        {"hour": "3", "minute": "0"},
    ),
    "precompute-engagement": (
        "precomputer.tasks.precompute_engagement",
        {"minute": "20"},
    ),
    "push-document-changes": (
        "precomputer.tasks.push_document_changes",
        {"minute": "*/5"},
    ),
    "detect-rfc-changes": (
        "subscriptions.tasks.detect_rfc_changes",
        {"minute": "25"},
    ),
    # After detection at :25, so the digest includes that hour's changes.
    "send-digest": (
        "subscriptions.tasks.send_digest",
        {"hour": "4", "minute": "30"},
    ),
    "sweep-unsent-notifications": (
        "subscriptions.tasks.sweep_unsent_notifications",
        {"minute": "40"},
    ),
}


def add_periodic_tasks(apps, schema_editor):
    CrontabSchedule = apps.get_model("django_celery_beat", "CrontabSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    for name, (task, fields) in PERIODIC_TASKS.items():
        if PeriodicTask.objects.filter(name=name).exists():
            continue
        spec = {
            "minute": "*",
            "hour": "*",
            "day_of_week": "*",
            "day_of_month": "*",
            "month_of_year": "*",
            "timezone": "UTC",
            **fields,
        }
        # No unique constraint on CrontabSchedule, so duplicates are possible.
        crontab = CrontabSchedule.objects.filter(**spec).first()
        if crontab is None:
            crontab = CrontabSchedule.objects.create(**spec)
        PeriodicTask.objects.create(name=name, task=task, crontab=crontab)


class Migration(migrations.Migration):
    dependencies = [
        ("precomputer", "0002_pendingdocumentchange"),
        ("django_celery_beat", "0018_improve_crontab_helptext"),
    ]

    operations = [
        migrations.RunPython(add_periodic_tasks, migrations.RunPython.noop),
    ]
