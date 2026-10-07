# Copyright The IETF Trust 2026, All Rights Reserved

from django.db import migrations

# Every scheduled task in the project, as name: (task, crontab fields) in UTC.
# Existing rows are not modified, so a schedule edited in the admin is kept.
PERIODIC_TASKS = {
    # Rebuilds and purges every file, as a floor for the targeted runs below.
    "precompute-all": (
        "precomputer.tasks.precompute_all",
        {"hour": "3", "minute": "0"},
    ),
    "precompute-engagement": (
        "precomputer.tasks.precompute_engagement",
        {"minute": "20"},
    ),
    # Must stay well above REEF_DOCUMENT_CHANGE_QUIET_SECONDS.
    "push-document-changes": (
        "precomputer.tasks.push_document_changes",
        {"minute": "*/5"},
    ),
    "detect-rfc-changes": (
        "subscriptions.tasks.detect_rfc_changes",
        {"minute": "25"},
    ),
    # After that hour's detection, so the digest holds everything staged since the
    # previous one.
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
