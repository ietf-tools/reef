# Copyright The IETF Trust 2026, All Rights Reserved
"""Backfill PopularEntry.rfc to the canonical identifier form.

The curated list predates reef.docids and stored whatever was typed into the
admin, so "9110" and "RFC 9110" are two entries for one document.
"""

from django.core.exceptions import ValidationError
from django.db import migrations

from reef.docids import normalize_doc_id


def canonicalize(apps, schema_editor):
    PopularEntry = apps.get_model("popularity", "PopularEntry")

    # Two passes, because rfc is already unique: renaming "9110" to "rfc9110"
    # collides with an entry that still literally holds "rfc9110" and has not
    # been reached yet. Every duplicate goes first, then the survivors are
    # renamed into the space that leaves.
    keepers, doomed = {}, []
    # By rank: where one document was listed twice, the higher placement wins,
    # which is what whoever curated the list meant.
    for row in PopularEntry.objects.order_by("rank", "id").iterator():
        try:
            rfc = normalize_doc_id(row.rfc, default_series="rfc")
        except ValidationError:
            # Not an identifier at all. Left for a curator to look at rather
            # than guessed at or dropped from the list.
            continue

        if rfc in keepers:
            doomed.append(row.pk)
        else:
            keepers[rfc] = row

    PopularEntry.objects.filter(pk__in=doomed).delete()
    for rfc, row in keepers.items():
        if rfc != row.rfc:
            row.rfc = rfc
            row.save(update_fields=["rfc"])


class Migration(migrations.Migration):
    dependencies = [("popularity", "0001_initial")]

    operations = [migrations.RunPython(canonicalize, migrations.RunPython.noop)]
