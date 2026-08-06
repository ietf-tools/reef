# Copyright The IETF Trust 2026, All Rights Reserved
"""Backfill Rating.rfc to the canonical identifier form.

Ratings predate reef.docids and stored whatever string they were handed, so a
rating of "9110" and a rating of "rfc9110" are two rows about one document.
"""

from django.core.exceptions import ValidationError
from django.db import migrations

from reef.docids import normalize_doc_id


def canonicalize(apps, schema_editor):
    Rating = apps.get_model("ratings", "Rating")

    # Two passes, because (rfc, user) is already unique: renaming "9110" to
    # "rfc9110" collides with a row that still literally holds "rfc9110" and has
    # not been reached yet. Every duplicate goes first, then the survivors are
    # renamed into the space that leaves.
    keepers, doomed = {}, []
    # Newest first: where one user rated the same document under two spellings,
    # their most recent rating is the one that survives the merge.
    for row in Rating.objects.order_by("-updated_at", "-id").iterator():
        try:
            rfc = normalize_doc_id(row.rfc, default_series="rfc")
        except ValidationError:
            # Not an identifier at all. Left alone rather than guessed at or
            # deleted; it simply will not join to anything.
            continue

        key = (row.user_id, rfc)
        if key in keepers:
            doomed.append(row.pk)
        else:
            keepers[key] = (row, rfc)

    Rating.objects.filter(pk__in=doomed).delete()
    for row, rfc in keepers.values():
        if rfc != row.rfc:
            row.rfc = rfc
            row.save(update_fields=["rfc"])


class Migration(migrations.Migration):
    dependencies = [("ratings", "0001_initial")]

    operations = [migrations.RunPython(canonicalize, migrations.RunPython.noop)]
