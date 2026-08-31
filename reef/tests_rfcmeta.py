# Copyright The IETF Trust 2026, All Rights Reserved
"""Tests for reading document metadata out of Red's published files.

Nothing here touches the network. What is being tested is mostly what happens when
Red's side is wrong or absent, which is the part that cannot be checked by pointing
it at the real thing and seeing a title come back.
"""

import datetime
import io
import json
import urllib.error
import zlib
from unittest import mock

import jsonschema
from django.test import SimpleTestCase, override_settings

from reef import rfcmeta

BASE_URL = "https://red.example.org"


def entry(**overrides):
    """One mini-index entry carrying every field Red's schema requires."""
    return {
        "number": 9110,
        "title": "HTTP Semantics",
        "status": {"slug": "std", "name": "internet standard"},
        "stream": {"slug": "IETF", "name": "IETF"},
        "authors": [{"titlepage_name": "R. Fielding"}],
        "formats": [],
        "subseries": [{"type": "std", "number": 97}],
        **overrides,
    }


def index_payload(entries=None, created_on="2026-08-31"):
    return {
        "createdOn": created_on,
        "miniIndex": [entry()] if entries is None else entries,
    }


def _response(payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    response = mock.MagicMock()
    response.__enter__.return_value = io.BytesIO(body)
    return response


@override_settings(REEF_RFC_DATA_BASE_URL=BASE_URL)
class LoadIndexTests(SimpleTestCase):
    def load(self, payload):
        with mock.patch("urllib.request.urlopen", return_value=_response(payload)):
            return rfcmeta.load_index()

    def test_a_valid_index_resolves_a_document(self):
        index = self.load(index_payload())
        self.assertEqual(len(index), 1)
        self.assertEqual(
            index.get("rfc9110"),
            {"title": "HTTP Semantics", "subseries": ["std97"]},
        )

    def test_the_url_is_built_from_the_configured_origin(self):
        with mock.patch(
            "urllib.request.urlopen", return_value=_response(index_payload())
        ) as urlopen:
            rfcmeta.load_index()
        self.assertEqual(
            urlopen.call_args.args[0], f"{BASE_URL}/api/v1/rfc-mini-index.json"
        )

    def test_a_trailing_slash_on_the_origin_does_not_double_up(self):
        with override_settings(REEF_RFC_DATA_BASE_URL=f"{BASE_URL}/"):
            with mock.patch(
                "urllib.request.urlopen", return_value=_response(index_payload())
            ) as urlopen:
                rfcmeta.load_index()
        self.assertEqual(
            urlopen.call_args.args[0], f"{BASE_URL}/api/v1/rfc-mini-index.json"
        )

    def test_a_field_red_adds_is_accepted(self):
        """The asymmetry this whole arrangement exists for. Red has undertaken to
        change these files additively, so a key Reef has never seen must pass; a
        schema that rejected it would turn every field Red adds into a Reef outage."""
        index = self.load(index_payload([entry(somethingNew={"a": 1})]))
        self.assertIsNotNone(index)
        self.assertEqual(index.get("rfc9110")["title"], "HTTP Semantics")

    def test_a_required_field_red_removes_is_refused(self):
        """The other half: a removal Reef depends on must not pass silently."""
        broken = entry()
        del broken["title"]
        with self.assertLogs("reef", level="ERROR") as logs:
            index = self.load(index_payload([broken]))
        self.assertIsNone(index)
        self.assertIn("no longer matches", "\n".join(logs.output))
        self.assertIn("title", "\n".join(logs.output))

    def test_the_error_names_where_the_break_is(self):
        """The fix is in Red or in the synced schema, neither of which is here, so
        the log has to say which field and where."""
        with self.assertLogs("reef", level="ERROR") as logs:
            self.load(index_payload([entry(title=42)]))
        self.assertIn("miniIndex/0/title", "\n".join(logs.output))

    def test_a_retyped_field_is_refused(self):
        with self.assertLogs("reef", level="ERROR"):
            self.assertIsNone(self.load(index_payload([entry(number="9110")])))

    def test_an_unreachable_red_yields_no_index_rather_than_raising(self):
        with mock.patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("refused")
        ):
            with self.assertLogs("reef", level="WARNING"):
                self.assertIsNone(rfcmeta.load_index())

    def test_a_timeout_yields_no_index(self):
        with mock.patch("urllib.request.urlopen", side_effect=TimeoutError):
            with self.assertLogs("reef", level="WARNING"):
                self.assertIsNone(rfcmeta.load_index())

    def test_a_body_that_is_not_json_yields_no_index(self):
        """A proxy error page arrives as a 200 with HTML in it."""
        with self.assertLogs("reef", level="WARNING"):
            self.assertIsNone(self.load(b"<html>502 Bad Gateway</html>"))

    def test_a_missing_created_on_is_refused_by_the_schema(self):
        payload = index_payload()
        del payload["createdOn"]
        with self.assertLogs("reef", level="ERROR"):
            self.assertIsNone(self.load(payload))

    def test_an_unparseable_created_on_leaves_the_age_unknown(self):
        """Rather than failing the index: a title with no age is still a title."""
        with self.assertLogs("reef", level="WARNING"):
            index = self.load(index_payload(created_on="not-a-date"))
        self.assertIsNotNone(index)
        self.assertIsNone(index.age_days)

    def test_an_entry_with_no_number_is_refused_by_the_schema(self):
        """DocumentIndex also skips such an entry, but it never gets that far: number
        is required, so validation refuses the whole index first."""
        payload = index_payload([entry(), entry(number=2119, title="Key words")])
        del payload["miniIndex"][0]["number"]
        with self.assertLogs("reef", level="ERROR"):
            self.assertIsNone(self.load(payload))


class MetaFromEntryTests(SimpleTestCase):
    def test_subseries_is_canonicalised_to_reef_identifiers(self):
        """So that a set holding "std97" and a title resolved from Red agree on the
        spelling."""
        meta = rfcmeta._meta_from_entry(entry())
        self.assertEqual(meta["subseries"], ["std97"])

    def test_a_document_in_no_subseries_gets_an_empty_list(self):
        self.assertEqual(rfcmeta._meta_from_entry(entry(subseries=[]))["subseries"], [])

    def test_a_missing_subseries_key_gets_an_empty_list(self):
        payload = entry()
        del payload["subseries"]
        self.assertEqual(rfcmeta._meta_from_entry(payload)["subseries"], [])

    def test_an_unparseable_subseries_is_dropped_and_the_title_survives(self):
        """The title is the part callers actually need, so one bad subseries entry
        does not cost them the document."""
        with self.assertLogs("reef", level="WARNING"):
            meta = rfcmeta._meta_from_entry(entry(subseries=[{"type": "nonsense"}]))
        self.assertEqual(meta["title"], "HTTP Semantics")
        self.assertEqual(meta["subseries"], [])


class DocumentIndexTests(SimpleTestCase):
    def index(self, created_on=None, entries=None):
        return rfcmeta.DocumentIndex(
            rfcmeta._reduce(entries if entries is not None else [entry()]),
            created_on or datetime.date.today(),
        )

    def test_a_miss_returns_none_and_is_remembered(self):
        index = self.index()
        self.assertIsNone(index.get("rfc8446"))
        self.assertEqual(index.misses, {"rfc8446"})

    def test_a_hit_is_not_recorded_as_a_miss(self):
        index = self.index()
        index.get("rfc9110")
        self.assertEqual(index.misses, set())

    def test_age_is_measured_from_created_on(self):
        index = self.index(
            created_on=datetime.date.today() - datetime.timedelta(days=9)
        )
        self.assertEqual(index.age_days, 9)

    def test_report_logs_the_size_and_age(self):
        with self.assertLogs("reef", level="INFO") as logs:
            self.index().report()
        self.assertIn("1 documents", "\n".join(logs.output))

    @override_settings(REEF_RFC_INDEX_MAX_AGE_DAYS=30)
    def test_report_warns_past_the_age_limit(self):
        index = self.index(
            created_on=datetime.date.today() - datetime.timedelta(days=31)
        )
        with self.assertLogs("reef", level="WARNING") as logs:
            index.report()
        self.assertIn("31 days old, over the 30 day limit", "\n".join(logs.output))

    @override_settings(REEF_RFC_INDEX_MAX_AGE_DAYS=30)
    def test_report_does_not_warn_inside_the_limit(self):
        """Red rebuilds when RFCs are published, not on a clock, and gaps of a
        fortnight are ordinary."""
        index = self.index(
            created_on=datetime.date.today() - datetime.timedelta(days=20)
        )
        with self.assertLogs("reef", level="INFO") as logs:
            index.report()
        self.assertNotIn("over the", "\n".join(logs.output))

    def test_report_names_the_documents_red_did_not_have(self):
        index = self.index()
        index.get("rfc8446")
        with self.assertLogs("reef", level="WARNING") as logs:
            index.report()
        output = "\n".join(logs.output)
        self.assertIn("1 document(s) Reef holds are not in Red's index", output)
        self.assertIn("rfc8446", output)

    def test_a_long_miss_list_is_truncated(self):
        """One line rather than a thousand."""
        index = self.index()
        for number in range(1000, 1020):
            index.get(f"rfc{number}")
        with self.assertLogs("reef", level="WARNING") as logs:
            index.report()
        self.assertIn("...", "\n".join(logs.output))

    def test_an_unknown_age_does_not_warn(self):
        index = rfcmeta.DocumentIndex(rfcmeta._reduce([entry()]), None)
        with self.assertLogs("reef", level="INFO") as logs:
            index.report()
        self.assertIn("unknown", "\n".join(logs.output))


@override_settings(REEF_RFC_DATA_BASE_URL=BASE_URL)
class ContainingSubseriesTests(SimpleTestCase):
    """What subscription matching uses to decide that a change to rfc2119 is also a
    change to bcp14."""

    def setUp(self):
        rfcmeta.clear_cache()
        self.addCleanup(rfcmeta.clear_cache)

    def warm(self, mapping):
        rfcmeta._memo["value"] = (mapping, None)
        rfcmeta._memo["expires"] = float("inf")

    def test_a_document_in_a_subseries_reports_it(self):
        self.warm({"rfc2119": {"title": "Key words", "subseries": ["bcp14"]}})
        self.assertEqual(rfcmeta.containing_subseries("rfc2119"), ["bcp14"])

    def test_a_document_in_none_reports_an_empty_list(self):
        self.warm({"rfc8446": {"title": "TLS 1.3", "subseries": []}})
        self.assertEqual(rfcmeta.containing_subseries("rfc8446"), [])

    def test_a_document_the_index_lacks_reports_an_empty_list(self):
        self.warm({})
        self.assertEqual(rfcmeta.containing_subseries("rfc9999"), [])

    def test_the_identifier_is_canonicalised(self):
        self.warm({"rfc2119": {"title": "Key words", "subseries": ["bcp14"]}})
        self.assertEqual(rfcmeta.containing_subseries("RFC 2119"), ["bcp14"])

    def test_an_unparseable_identifier_reports_an_empty_list(self):
        self.warm({})
        self.assertEqual(rfcmeta.containing_subseries("nonsense"), [])

    def test_the_caller_cannot_mutate_the_shared_index(self):
        """It hands back a copy: the mapping behind it is shared with every other
        caller in this process."""
        mapping = {"rfc2119": {"title": "Key words", "subseries": ["bcp14"]}}
        self.warm(mapping)
        rfcmeta.containing_subseries("rfc2119").append("std99")
        self.assertEqual(mapping["rfc2119"]["subseries"], ["bcp14"])

    def test_it_fetches_when_the_index_is_not_loaded(self):
        """Unlike a display read: skipping the expansion costs somebody an email."""
        with mock.patch(
            "urllib.request.urlopen", return_value=_response(index_payload())
        ) as urlopen:
            rfcmeta.containing_subseries("rfc9110")
        self.assertEqual(urlopen.call_count, 1)

    def test_an_unreachable_red_warns_and_expands_nothing(self):
        with mock.patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("refused")
        ):
            with self.assertLogs("reef", level="WARNING") as logs:
                self.assertEqual(rfcmeta.containing_subseries("rfc2119"), [])
        self.assertIn("without expanding subseries", "\n".join(logs.output))


class SyncedSchemaTests(SimpleTestCase):
    """The schema file itself, which is a copy of Red's and easy to break in a sync."""

    def test_it_is_a_valid_draft_2020_12_schema(self):
        jsonschema.Draft202012Validator.check_schema(rfcmeta._schema())

    def test_it_requires_the_fields_reef_reads(self):
        item = rfcmeta._schema()["properties"]["miniIndex"]["items"]
        self.assertIn("number", item["required"])
        self.assertIn("title", item["required"])

    def test_it_does_not_forbid_unknown_properties(self):
        """Exported from Zod with io='input' for exactly this reason. If this ever
        becomes False, every field Red adds breaks Reef."""
        item = rfcmeta._schema()["properties"]["miniIndex"]["items"]
        self.assertNotIn("additionalProperties", item)


@override_settings(REEF_RFC_DATA_BASE_URL=BASE_URL)
class SharedCacheTests(SimpleTestCase):
    """The layers between a caller and Red: a process memo over a shared cache.

    Both are global, so every test here clears them. Leaving one warm would leak an
    index into whatever test ran next, which is the kind of order-dependent failure
    that takes an afternoon to find.
    """

    def setUp(self):
        rfcmeta.clear_cache()
        self.addCleanup(rfcmeta.clear_cache)

    def urlopen(self, payload=None):
        return mock.patch(
            "urllib.request.urlopen",
            return_value=_response(payload if payload is not None else index_payload()),
        )

    def test_the_first_call_fetches_and_the_second_does_not(self):
        with self.urlopen() as urlopen:
            self.assertIsNotNone(rfcmeta.get_index())
            self.assertIsNotNone(rfcmeta.get_index())
        self.assertEqual(urlopen.call_count, 1)

    def test_a_second_process_reads_the_shared_cache_rather_than_red(self):
        """The memo is per-process; the cache is what stops a cold worker refetching."""
        with self.urlopen() as urlopen:
            rfcmeta.get_index()
        rfcmeta._memo["value"] = None  # as a freshly started process would find it
        with self.urlopen() as second:
            index = rfcmeta.get_index()
        self.assertEqual(index.get("rfc9110")["title"], "HTTP Semantics")
        self.assertEqual(second.call_count, 0)
        self.assertEqual(urlopen.call_count, 1)

    def test_the_cached_entry_is_compressed(self):
        """784 KiB pickled against memcached's 1 MiB item cap, and a store over the
        cap fails silently. Compressed it is 209 KiB."""
        from django.core.cache import cache

        with self.urlopen():
            rfcmeta.get_index()
        blob = cache.get(rfcmeta.CACHE_KEY)
        self.assertIsInstance(blob, bytes)
        self.assertEqual(json.loads(zlib.decompress(blob))["created_on"], "2026-08-31")

    def test_an_unreadable_cache_entry_is_discarded_and_refetched(self):
        from django.core.cache import cache

        cache.set(rfcmeta.CACHE_KEY, b"not compressed json")
        with self.assertLogs("reef", level="WARNING"):
            with self.urlopen() as urlopen:
                self.assertIsNotNone(rfcmeta.get_index())
        self.assertEqual(urlopen.call_count, 1)

    def test_a_failed_fetch_is_not_memoised(self):
        """Memoising a failure would keep a transient outage in front of every caller
        for a minute after it had cleared."""
        with mock.patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("refused")
        ):
            with self.assertLogs("reef", level="WARNING"):
                self.assertIsNone(rfcmeta.get_index())
        with self.urlopen():
            self.assertIsNotNone(rfcmeta.get_index())

    def test_each_caller_gets_its_own_miss_tracking(self):
        """One run's unresolved documents must not land in another's report."""
        with self.urlopen():
            first = rfcmeta.get_index()
        first.get("rfc8446")
        second = rfcmeta.get_index()
        self.assertEqual(first.misses, {"rfc8446"})
        self.assertEqual(second.misses, set())

    def test_cached_mapping_never_fetches(self):
        """A page render must not wait on a 6.8 MB download, and a test that touches
        an admin page must not reach the network at all."""
        with mock.patch("urllib.request.urlopen") as urlopen:
            self.assertIsNone(rfcmeta.cached_mapping())
        urlopen.assert_not_called()

    def test_cached_mapping_uses_a_warm_index(self):
        with self.urlopen():
            rfcmeta.get_index()
        with mock.patch("urllib.request.urlopen") as urlopen:
            mapping = rfcmeta.cached_mapping()
        self.assertEqual(mapping["rfc9110"]["title"], "HTTP Semantics")
        urlopen.assert_not_called()

    def test_clear_cache_drops_both_layers(self):
        with self.urlopen():
            rfcmeta.get_index()
        rfcmeta.clear_cache()
        with mock.patch("urllib.request.urlopen") as urlopen:
            self.assertIsNone(rfcmeta.cached_mapping())
        urlopen.assert_not_called()
