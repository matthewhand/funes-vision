"""Canonical HA taxonomy / timezone source of truth (#58).

taxonomy.py is the single Python source for the HA flag set, string parents
and sentinels, dropped flags and display-timezone resolution. catalog.py and
scans.py re-export from it; /api/taxonomy hands the same payload to the SPA.
The JS half of the parity check lives in tests/test_taxonomy_parity.js.
"""
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api_server
import catalog
import scans
import taxonomy


class FakeHandler:
    """Drive api_server.Handler.do_GET with captured JSON responses."""

    def __init__(self):
        self.sent = []
        self.handler = api_server.Handler.__new__(api_server.Handler)

    def _send(self, code, obj):
        self.sent.append((code, obj))

    def _send_response(self, code, message=None):
        pass

    def _send_header(self, key, value):
        pass

    def _end_headers(self):
        pass

    def get(self, path):
        h = self.handler
        self.sent = []
        h._send = self._send
        h.send_response = self._send_response
        h.send_header = self._send_header
        h.end_headers = self._end_headers
        h.path = path
        h.headers = {}
        h.rfile = io.BytesIO(b"")
        h.wfile = io.BytesIO()
        h.do_GET()
        return self.sent[-1] if self.sent else None


class TestCanonicalConstants(unittest.TestCase):
    def test_payload_matches_module_constants(self):
        p = taxonomy.payload()
        self.assertEqual(p["ha_flag_keys"], list(taxonomy.HA_FLAG_KEYS))
        self.assertEqual(p["ha_string_sentinels"], list(taxonomy.HA_STRING_SENTINELS))
        self.assertEqual(p["ha_string_parents"],
                         {k: list(v) for k, v in taxonomy.HA_STRING_PARENTS.items()})
        self.assertEqual(p["dropped_flags"], list(taxonomy.DROPPED_FLAGS))
        self.assertEqual(p["default_tz"], taxonomy.DEFAULT_TZ)

    def test_payload_is_json_serialisable(self):
        # The SPA fetches this exact object.
        self.assertEqual(json.loads(json.dumps(taxonomy.payload())), taxonomy.payload())

    def test_flag_set_matches_the_live_activity_schema(self):
        # analyze_images derives its schema keys from FRONT/BACK; keep the
        # taxonomy list in lockstep without importing cv2 here.
        import analyze_images
        self.assertEqual(set(taxonomy.HA_FLAG_KEYS),
                         set(analyze_images.LLM_ACTIVITY_KEYS))

    def test_ha_flags_disjoint_from_detector_labels(self):
        self.assertFalse(set(taxonomy.HA_FLAG_KEYS) & set(taxonomy.YOLO_PRESENCE_KEYS))

    def test_string_parents_are_known_boolean_flags(self):
        for child, parents in taxonomy.HA_STRING_PARENTS.items():
            self.assertIn(child, taxonomy.HA_FLAG_KEYS)
            for parent in parents:
                self.assertIn(parent, taxonomy.HA_FLAG_KEYS)


class TestReExports(unittest.TestCase):
    def test_scans_dropped_flags_is_canonical(self):
        self.assertIs(scans.DROPPED_FLAGS, taxonomy.DROPPED_FLAGS)

    def test_catalog_label_sets_are_canonical(self):
        self.assertIs(catalog.YOLO_PRESENCE_KEYS, taxonomy.YOLO_PRESENCE_KEYS)
        self.assertIs(catalog.GATE_IGNORE_LABELS, taxonomy.GATE_IGNORE_LABELS)


class TestResolveTimezone(unittest.TestCase):
    def test_env_overrides_settings(self):
        self.assertEqual(taxonomy.resolve_timezone("UTC", "Australia/Sydney"), "UTC")

    def test_settings_used_without_env(self):
        self.assertEqual(taxonomy.resolve_timezone(None, "Europe/London"), "Europe/London")

    def test_default_when_neither(self):
        self.assertEqual(taxonomy.resolve_timezone(None, None), taxonomy.DEFAULT_TZ)

    def test_blank_values_ignored(self):
        self.assertEqual(taxonomy.resolve_timezone("", "  "), taxonomy.DEFAULT_TZ)
        self.assertEqual(taxonomy.resolve_timezone("  ", "America/New_York"), "America/New_York")

    def test_api_server_uses_the_canonical_resolver(self):
        self.assertIs(api_server.resolve_timezone, taxonomy.resolve_timezone)


class TestTaxonomyEndpoint(unittest.TestCase):
    def setUp(self):
        self._orig_watch = api_server.WATCH_DIRS
        api_server.WATCH_DIRS = []
        self.h = FakeHandler()

    def tearDown(self):
        api_server.WATCH_DIRS = self._orig_watch

    def test_returns_canonical_payload(self):
        code, body = self.h.get("/api/taxonomy")
        self.assertEqual(code, 200)
        self.assertEqual(body, taxonomy.payload())
        self.assertIn("porch_access", body["ha_flag_keys"])
        self.assertIn("approaching_house", body["dropped_flags"])
        self.assertEqual(body["default_tz"], "Australia/Sydney")

    def test_camera_query_is_ignored(self):
        code, body = self.h.get("/api/taxonomy?camera=all")
        self.assertEqual(code, 200)
        self.assertEqual(body, taxonomy.payload())


if __name__ == "__main__":
    unittest.main()
