# Copyright The IETF Trust 2026, All Rights Reserved
"""The published schema, and the strictness Reef holds itself to.

The test that matters here is not that a good payload validates -- the precompute
run does that on every execution. It is that a bad one does not, because a
strict() that quietly returned its argument unchanged would look exactly like a
strict() that worked, for as long as the payload happened to be correct.
"""

import copy
import json
from pathlib import Path

import jsonschema
from django.test import TestCase

from precomputer import schemas

PAYLOAD = {
    "documents": {
        "rfc9110": {"title": "HTTP Semantics", "subseries": ["std97"]},
        "rfc2119": {"title": None, "subseries": []},
    },
    "subjects": {
        "messaging": {
            "id": 1,
            "name": "Messaging",
            "description": "",
            "parent": None,
            "path": "messaging",
            "children": ["email"],
            "documents": ["rfc2119"],
            "document_count": 1,
            "document_count_deep": 2,
        },
        "email": {
            "id": 2,
            "name": "Email",
            "description": "Anything email.",
            "parent": "messaging",
            "path": "messaging/email",
            "children": [],
            "documents": ["rfc9110"],
            "document_count": 1,
            "document_count_deep": 1,
        },
    },
}


class CommittedSchemaTests(TestCase):
    def test_the_committed_file_is_the_artifact_red_gets(self):
        # Loaded from disk rather than built, because the file is the source and
        # the thing that gets copied.
        raw = json.loads(
            (Path(schemas.SCHEMA_DIR) / "subjects.schema.json").read_text()
        )
        self.assertEqual(schemas.load("subjects"), raw)

    def test_it_is_draft_2020_12(self):
        # The draft rfcmeta already validates with, so both directions agree.
        self.assertEqual(
            schemas.load("subjects")["$schema"],
            "https://json-schema.org/draft/2020-12/schema",
        )

    def test_the_committed_schema_accepts_a_payload(self):
        jsonschema.Draft202012Validator(schemas.load("subjects")).validate(PAYLOAD)

    def test_the_committed_schema_permits_a_key_it_has_not_seen(self):
        """Reef owes Red the additive guarantee Red gives Reef.

        The copy Red validates with must not reject a key Reef adds later, or every
        additive change becomes an outage at the far end.
        """
        payload = copy.deepcopy(PAYLOAD)
        payload["subjects"]["email"]["colour"] = "green"
        payload["generated_at"] = "2026-01-01"
        jsonschema.Draft202012Validator(schemas.load("subjects")).validate(payload)


class StrictnessTests(TestCase):
    def test_a_valid_payload_still_passes(self):
        schemas.validate("subjects", PAYLOAD)

    def test_a_key_the_schema_does_not_declare_is_rejected(self):
        # The test the whole approach rests on. Without it, strict() could regress
        # to returning its argument and nothing would notice.
        payload = copy.deepcopy(PAYLOAD)
        payload["subjects"]["email"]["colour"] = "green"
        with self.assertRaises(jsonschema.ValidationError):
            schemas.validate("subjects", payload)

    def test_a_stray_top_level_key_is_rejected(self):
        payload = copy.deepcopy(PAYLOAD)
        payload["generated_at"] = "2026-01-01"
        with self.assertRaises(jsonschema.ValidationError):
            schemas.validate("subjects", payload)

    def test_a_stray_key_in_document_metadata_is_rejected(self):
        payload = copy.deepcopy(PAYLOAD)
        payload["documents"]["rfc9110"]["status"] = "Internet Standard"
        with self.assertRaises(jsonschema.ValidationError):
            schemas.validate("subjects", payload)

    def test_maps_with_arbitrary_keys_still_pass(self):
        """The rule that makes strict() safe.

        documents and subjects are typed by giving additionalProperties a schema,
        which is how a map with arbitrary keys is expressed. Overwriting that with
        false would forbid every document rather than constrain it, so a tightening
        that broke this would reject every real payload.
        """
        schemas.validate("subjects", PAYLOAD)
        self.assertEqual(len(PAYLOAD["documents"]), 2)

    def test_the_documents_map_keeps_its_value_schema(self):
        tightened = schemas.strict(schemas.load("subjects"))
        documents = tightened["properties"]["documents"]
        # Still a schema, not False: this is the map rule, checked on the tightened
        # schema rather than only through its effect on a payload. The entry schema
        # is reached by $ref, so the tightening has to have walked $defs to get it.
        self.assertEqual(
            documents["additionalProperties"],
            {"$ref": "#/$defs/documentMetadata"},
        )
        self.assertIs(
            tightened["$defs"]["documentMetadata"]["additionalProperties"], False
        )

    def test_a_missing_required_key_is_rejected(self):
        payload = copy.deepcopy(PAYLOAD)
        del payload["subjects"]["email"]["path"]
        with self.assertRaises(jsonschema.ValidationError):
            schemas.validate("subjects", payload)

    def test_an_identifier_without_its_series_is_rejected(self):
        # The rule the model enforces on the way in, held to on the way out.
        payload = copy.deepcopy(PAYLOAD)
        payload["subjects"]["email"]["documents"] = ["9110"]
        with self.assertRaises(jsonschema.ValidationError):
            schemas.validate("subjects", payload)

    def test_an_unknown_keyword_raises_rather_than_passing_through(self):
        # A keyword nobody taught strict() about is a branch where the tightening
        # silently stopped applying, so it has to be loud.
        with self.assertRaises(ValueError):
            schemas.strict({"type": "object", "dependentSchemas": {}})
