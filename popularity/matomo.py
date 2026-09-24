# Copyright The IETF Trust 2026, All Rights Reserved
"""Reduce a Matomo page-URL export to a ranking of RFCs.

Pure functions, no logging, no Django. The export holds personal data beside the
page URLs -- visitor emails under /person, login redirects, tracking strings -- and
this module is where it is read, so nothing here keeps or reports a label: a row
that does not name an RFC becomes a count, never a string.

The input is Matomo's ``Actions.getPageUrls`` report, expanded: a list of rows with
``label`` and ``nb_visits``, where a directory row carries its children in
``subtable`` and its own ``nb_visits`` already sums them. A directory's label has
no leading slash and a page's does, so ``/info/rfc9110`` (its query-string variants
are the children) and ``/rfc/rfc9110.txt`` both appear one level under their
directory, and neither is descended into.
"""

import dataclasses
import re

# The ways of reading a document on rfc-editor.org: the info page, and the text in
# any format. Anything else naming an RFC -- index ranges, prereleases, DOI links,
# translation proxies -- is a link to a document rather than a read of it.
COUNTED_DIRECTORIES = ("info", "rfc", "pdfrfc")

# Matomo folds the rows past its archiving cap into one labelled this. Its
# presence means the directory's ranking stops at the documents Matomo kept.
OTHERS_LABEL = "Others"

_RFC_LABEL = re.compile(r"^/?rfc0*(\d+)(?:\.[\w.]+)?$", re.IGNORECASE)


@dataclasses.dataclass
class MatomoParse:
    scores: dict[str, float]
    rows_seen: int = 0
    rows_ignored: int = 0
    truncated: list[str] = dataclasses.field(default_factory=list)


def _rows(value):
    return value if isinstance(value, list) else []


def _label(row):
    return row.get("label") if isinstance(row, dict) else None


def _visits(row):
    visits = row.get("nb_visits", 0)
    return visits if isinstance(visits, int) and visits > 0 else 0


def parse_rankings(payload):
    """Sum visits per RFC across the counted directories and rank them."""
    visits = {}
    parse = MatomoParse(scores={})
    for directory in _rows(payload):
        if _label(directory) not in COUNTED_DIRECTORIES:
            continue
        for row in _rows(directory.get("subtable")):
            parse.rows_seen += 1
            label = _label(row)
            if label == OTHERS_LABEL:
                parse.truncated.append(directory["label"])
                parse.rows_ignored += 1
                continue
            match = _RFC_LABEL.match(label) if isinstance(label, str) else None
            if match is None:
                parse.rows_ignored += 1
                continue
            rfc = f"rfc{int(match[1])}"
            visits[rfc] = visits.get(rfc, 0) + _visits(row)
    parse.scores = percentile(visits)
    return parse


def percentile(visits):
    """Rank percentile: the share of other documents strictly below each one.

    The most visited scores 1.0 and the least 0.0, ties score alike, and a lone
    document scores 1.0. Only ordering survives: the ratio of traffic between two
    documents cannot be read back from the result.
    """
    if not visits:
        return {}
    if len(visits) == 1:
        return dict.fromkeys(visits, 1.0)
    ordered = sorted(visits.items(), key=lambda item: item[1])
    below = 0
    scores = {}
    for index, (rfc, count) in enumerate(ordered):
        if index and count > ordered[index - 1][1]:
            below = index
        scores[rfc] = below / (len(ordered) - 1)
    return scores
