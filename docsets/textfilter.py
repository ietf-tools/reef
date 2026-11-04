# Copyright The IETF Trust 2026, All Rights Reserved
"""Strip what spam in a set's title and description is made of."""

import re

# `(?a:\b)` keeps word boundaries ASCII-only so that a letter outside a-z
# neither starts nor ends a match; whitespace stays Unicode-aware.
_BOUNDARY = r"(?a:\b)"

# A scheme (http:, mailto:, magnet:, ...), a www. host, or a bare host with a
# letters-only TLD, each followed by whatever non-space characters trail it.
_URI = re.compile(
    rf"{_BOUNDARY}[a-z][a-z0-9+.-]*:\S+"
    rf"|{_BOUNDARY}www\.\S+"
    rf"|{_BOUNDARY}(?:[a-z0-9-]+\.)+[a-z]{{2,}}(?:[/:?#]\S*)?",
    re.IGNORECASE | re.ASCII,
)

# Cryptocurrency addresses (Bitcoin, Ethereum's 0x form, bech32, Monero, ...) are
# all single unbroken alphanumeric tokens far longer than any word, so word length
# is the signal. The shortest, legacy Bitcoin, is 26 characters.
_CRYPTO_ADDRESS = re.compile(
    rf"{_BOUNDARY}[a-z0-9]{{26,}}{_BOUNDARY}", re.IGNORECASE | re.ASCII
)

_WHITESPACE_RUN = re.compile(r"\s{2,}")


def filter_spam_text(text: str) -> str:
    """Remove URIs and cryptocurrency addresses, then tidy the whitespace left."""
    text = _URI.sub("", text)
    text = _CRYPTO_ADDRESS.sub("", text)
    return _WHITESPACE_RUN.sub(" ", text).strip()


# Serializes `title` and `description` through filter_spam_text. Output only: what
# a caller stored is kept as written, and a write's response shows what a reader
# would now be served. A comment, not a docstring: drf-spectacular would publish a
# docstring as the description of every schema that inherits it.
class FilteredSetTextMixin:
    def to_representation(self, instance):
        data = super().to_representation(instance)
        for field in ("title", "description"):
            if field in data:
                data[field] = filter_spam_text(data[field])
        return data
