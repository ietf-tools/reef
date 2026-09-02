# Copyright The IETF Trust 2026, All Rights Reserved
"""The schemas Reef publishes, and the strictness it holds itself to.

Reef writes these files, so Reef owns their shape. reef/schemas/ is the mirror
of this and holds the other direction: schemas for data Reef reads, owned
elsewhere and copied in.

The committed .json is the artifact, not an export of something Python-shaped.
It has to be language agnostic because Red validates against a copy of it and
derives its Zod from that copy, so putting a Pydantic model or a DRF serializer
between Reef and the file would make the shared thing a by-product of one
consumer's language.

What a hand-written schema normally costs is drift -- a file describing what
somebody believed the builder emitted. That is answered here by where it is
enforced rather than by generating it: the precompute run validates its own
output on every execution, so a builder that drifts fails the next run instead
of whenever a test is remembered.

Permissive as a consumer, strict as a producer
----------------------------------------------
The committed file carries no additionalProperties: false, and Red validates
with it exactly as committed. reef/schemas/README.md argues this from the other
side -- rejecting unknown keys "would turn every field Red adds into a Reef
outage" -- and Reef is the producer now, so it owes Red the same additive
guarantee it is given. One artifact, permissive, copied verbatim, and no strict
variant in the repository that could one day be the copy that gets sent.

Reef's own use of that file is strict, and nothing about the file changes for it
to be: jsonschema validates against a schema object, so the run tightens the
loaded copy in memory. A key Reef did not mean to write is a bug in Reef rather
than somebody else's additive change, and it should stop the run.
"""

import functools
import json
from pathlib import Path

import jsonschema

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

# Keywords whose value is a schema, and keywords whose value is a map or list of
# them. Named rather than inferred so that a keyword nobody taught this about
# raises instead of passing through untightened, which is how strictness stops
# applying to a branch without anybody noticing.
_SUBSCHEMA = (
    "additionalProperties",
    "items",
    "not",
    "propertyNames",
    "if",
    "then",
    "else",
)
_SUBSCHEMA_MAP = ("properties", "$defs", "definitions", "patternProperties")
_SUBSCHEMA_LIST = ("allOf", "anyOf", "oneOf", "prefixItems")
# Carry no subschemas: values are literals, lists of literals, or plain data.
_LEAF = (
    "$schema",
    "$id",
    "$ref",
    "$comment",
    "title",
    "description",
    "type",
    "required",
    "enum",
    "const",
    "default",
    "examples",
    "format",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    "deprecated",
    "readOnly",
    "writeOnly",
)


@functools.cache
def load(name):
    """The committed schema, exactly as Red gets it."""
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text())


def strict(schema):
    """A copy that also refuses keys the schema does not declare.

    One rule matters: additionalProperties: false goes only on an object that
    declares properties and carries neither additionalProperties nor
    patternProperties of its own. Setting it everywhere would break a map with
    arbitrary keys, which is typed precisely by giving additionalProperties a
    schema -- overwriting that with false forbids every entry rather than
    constraining it.
    """
    if not isinstance(schema, dict):
        return schema

    tightened = {}
    for keyword, value in schema.items():
        if keyword in _SUBSCHEMA:
            tightened[keyword] = strict(value)
        elif keyword in _SUBSCHEMA_MAP:
            tightened[keyword] = {
                key: strict(subschema) for key, subschema in value.items()
            }
        elif keyword in _SUBSCHEMA_LIST:
            tightened[keyword] = [strict(subschema) for subschema in value]
        elif keyword in _LEAF:
            tightened[keyword] = value
        else:
            raise ValueError(
                f"strict() does not know the keyword {keyword!r}, so it cannot say "
                "whether it holds a subschema. Teach it rather than letting the "
                "branch through untightened."
            )

    declares_properties = "properties" in tightened
    has_own_open_policy = (
        "additionalProperties" in tightened or "patternProperties" in tightened
    )
    if declares_properties and not has_own_open_policy:
        tightened["additionalProperties"] = False
    return tightened


def validate(name, payload):
    """Check a payload Reef is about to publish, strictly.

    Raises jsonschema.ValidationError, which the run turns into a failure rather
    than an upload: publishing something Red will reject takes Red's page down,
    and declining to publish leaves it one run behind.
    """
    jsonschema.Draft202012Validator(strict(load(name))).validate(payload)
