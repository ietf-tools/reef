# Copyright The IETF Trust 2026, All Rights Reserved
"""Jev (TypeSafe AI) as a spam classifier: text in, probability of spam out.

No Django imports. Jev is a hosted service with no self-hosted form, so the whole of
what leaves Reef is built here, in one place: the text, a fixed question and a model
name. Never an account identifier, address or anything else about who wrote it.
"""

import json
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"

# Pinned rather than an alias such as jev-latest: the threshold is chosen from
# moderators' labels against this model's scores, and an alias would change the
# scores under it.
MODEL = "jev-1.13.0"

# The identifier a score is stored under, so that a change of model never
# overwrites the scores of another.
CLASSIFIER = f"jev:{MODEL}"

QUESTION = "spam"

# The model sees the text as data and this as the question. It judges what the
# text is for, not which words it contains, which is why Reef carries no term list.
INSTRUCTIONS = (
    "Is this text spam: unsolicited advertising or promotion, a link or contact "
    "detail pushed at the reader, a scam, or automated filler, rather than a genuine "
    "contribution from a person engaging with the subject?"
)

# Well inside the model's context. Spam is not improved by length, and a response
# can hold a pasted document.
MAX_TEXT_CHARS = 20_000

TIMEOUT_SECONDS = 20


def spam_probability(text, api_key):
    """The probability, 0 to 1, that text is spam.

    Raises urllib.error.URLError, HTTPError included, if the service cannot be
    reached or refuses, and ValueError if it answers in a shape this does not
    expect. An unexpected answer is an error rather than a guess: a score that
    could mean anything must not be recorded as clean.
    """
    body = json.dumps(
        {
            "model": MODEL,
            "state": text[:MAX_TEXT_CHARS],
            "questions": {
                QUESTION: {"type": "noul", "instructions": INSTRUCTIONS},
            },
        }
    ).encode()
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        payload = json.load(response)
    try:
        probability = payload["answers"][QUESTION]["noul"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Jev answered in an unexpected shape") from exc
    if (
        isinstance(probability, bool)
        or not isinstance(probability, int | float)
        or not 0 <= probability <= 1
    ):
        raise ValueError("Jev gave a probability outside 0 to 1")
    return float(probability)
