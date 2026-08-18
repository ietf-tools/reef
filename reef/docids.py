# Copyright The IETF Trust 2026, All Rights Reserved
"""Document identifiers, in one canonical form.

Ratings, popularity, document sets and subscriptions all key on a published
document, and a set cannot be joined to its documents' ratings or subscriptions
unless every one of them spells an identifier the same way. That form is the
series followed by the number, lowercase, with no separator and no leading
zeros: rfc9110, bcp14, std66.

The series prefix is what keeps an identifier unambiguous: bcp14 and rfc14 are
different documents, so a bare number only means something in a context that
supplies the series (see default_series).
"""

import re

from django.core.exceptions import ValidationError

# Published series a document identifier can name. The subseries are containers
# whose membership changes over time (BCP 14 is currently RFC 2119 plus
# RFC 8174), which matters when matching a change event: see the open items in
# plan.md.
DOC_SERIES = ("rfc", "bcp", "std", "fyi")

DOC_ID_MAX_LENGTH = 32

_DOC_ID_RE = re.compile(
    rf"^(?:({'|'.join(DOC_SERIES)})[\s_-]*)?0*(\d+)$",
    re.IGNORECASE,
)

# The canonical form itself, for reading one back apart. Anchored and
# case-sensitive, unlike _DOC_ID_RE, because what it matches is this module's
# own output rather than what someone typed.
_CANONICAL_DOC_ID_RE = re.compile(rf"^({'|'.join(DOC_SERIES)})(\d+)$")


def normalize_doc_id(value, default_series=None):
    """Return value in canonical form, or raise ValidationError.

    Accepts the shapes people paste ("RFC 9110", "bcp-0014") and returns one
    form ("rfc9110", "bcp14").

    A bare number is rejected unless default_series names the series to read it
    as. Callers whose context is already a single series pass one: the ratings
    of an RFC are about an RFC, so /ratings/9110/ is unambiguous. Callers that
    accept documents from any series pass nothing, so that "14" has to be
    written out as bcp14 or rfc14.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("A document identifier is required.")

    match = _DOC_ID_RE.match(value.strip())
    if not match:
        raise ValidationError(
            f"Not a document identifier: {value!r}. "
            f"Give the series and number, for example rfc9110."
        )

    series = (match[1] or default_series or "").lower()
    if not series:
        raise ValidationError(
            f"Ambiguous document identifier: {value!r}. "
            f"Name the series, for example rfc{match[2]} or bcp{match[2]}."
        )

    doc_id = f"{series}{match[2]}"
    if len(doc_id) > DOC_ID_MAX_LENGTH:
        raise ValidationError(
            f"A document identifier must be at most {DOC_ID_MAX_LENGTH} characters."
        )
    return doc_id


def display_doc_id(value):
    """Return a canonical identifier as prose writes it: rfc9110 -> "RFC 9110".

    The canonical form exists so that storage and joins have one spelling; it
    is not how the series is written in a sentence. Anything that does not
    parse comes back unchanged, so that a caller rendering a value from an
    external feed shows it as given rather than losing it.
    """
    match = _CANONICAL_DOC_ID_RE.match(value or "")
    if not match:
        return value
    return f"{match[1].upper()} {match[2]}"
