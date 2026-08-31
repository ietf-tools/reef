# Copyright The IETF Trust 2026, All Rights Reserved
"""Which documents a survey is offered on.

Red renders a survey invite as a toast, and a toast that appears on every page of the
site is worse than no toast at all. So a survey can name the documents it is about,
and Red shows it only when the reader is on one of them.

Two ways to name them, because staff think in both: a list of identifiers for a
survey about particular RFCs, and a list of subject slugs for one about a topic. The
second resolves through the subject vocabulary at read time rather than at save, so a
document assigned to the subject next week falls inside the audience without anybody
reopening the survey -- the same way a set or subject subscription matches membership
as it stands when the change lands.

Targeting is separate from visibility. It decides where a survey is shown, not who
may take it; an authenticated-visibility survey targeted at one RFC is still refused
to an anonymous caller on that RFC's page.
"""

import logging

from django.core.exceptions import ValidationError

from reef.docids import normalize_doc_id
from subjects.models import SubjectAssignment

logger = logging.getLogger("reef")

AUDIENCE_KEYS = ("documents", "subjects")


def validate_audience(audience):
    """Raise ValidationError unless this is a shape the resolver understands.

    Checked on save rather than trusted, because the field is free-form JSON that
    staff type: a misspelt key would otherwise target nothing while looking targeted.
    """
    if audience is None:
        return
    if not isinstance(audience, dict):
        raise ValidationError("audience must be an object, or null for no targeting.")
    if unknown := sorted(set(audience) - set(AUDIENCE_KEYS)):
        raise ValidationError(
            f"Unknown audience key(s): {', '.join(unknown)}. "
            f"Expected {' and '.join(AUDIENCE_KEYS)}."
        )
    for key in AUDIENCE_KEYS:
        value = audience.get(key)
        if value is not None and not isinstance(value, list):
            raise ValidationError(f"audience.{key} must be a list.")
    for raw in audience.get("documents") or []:
        normalize_doc_id(raw)  # raises ValidationError, naming the identifier


def normalize_audience(audience):
    """Canonicalise the identifiers staff typed, so they join to what Reef stores."""
    if not isinstance(audience, dict):
        return audience
    normalized = dict(audience)
    if documents := audience.get("documents"):
        normalized["documents"] = [normalize_doc_id(raw) for raw in documents]
    return normalized


def is_targeted(audience):
    """Whether this survey names an audience at all.

    Naming one that currently resolves to nothing is still targeting: a survey aimed
    at a subject with no documents yet should appear nowhere, not everywhere, and
    those two are only distinguishable here.
    """
    if not isinstance(audience, dict):
        return False
    return any(audience.get(key) for key in AUDIENCE_KEYS)


def resolve_audience(audience):
    """The documents a survey is offered on, or None if it is offered everywhere.

    None and the empty list mean different things, deliberately. None is "not
    targeted, show it anywhere"; [] is "targeted, but nothing currently matches",
    which is what a subject with no documents assigned to it yet looks like.
    """
    if not is_targeted(audience):
        return None

    documents = set()
    for raw in audience.get("documents") or []:
        try:
            documents.add(normalize_doc_id(raw))
        except ValidationError:
            # Saved before validation existed, or edited around it. One bad
            # identifier should not cost the survey the rest of its audience.
            logger.warning("Unparseable document in survey audience: %r", raw)

    if slugs := audience.get("subjects"):
        documents |= set(
            SubjectAssignment.objects.filter(subject__slug__in=slugs).values_list(
                "doc", flat=True
            )
        )

    # Sorted because this is published in a precomputed file, which has to be the
    # same bytes when nothing has changed.
    return sorted(documents)
