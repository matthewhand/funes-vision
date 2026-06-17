"""Baseline unit tests for the webcam project's pure helpers.

Run from the repo root:  python3 -m unittest discover -s tests
No third-party deps — stdlib unittest only. These cover the small, pure
logic units; DOM/UI behaviour is proven separately (extracted pure fns +
the deployed app).
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_server
from integrations import slack, media
import integrations as intg


class TestSettingValid(unittest.TestCase):
    def test_choice_ok(self):
        self.assertTrue(api_server.setting_valid("fast_pass_engine", "yolo"))
        self.assertTrue(api_server.setting_valid("deep_passes_enabled", False))

    def test_choice_bad(self):
        self.assertFalse(api_server.setting_valid("fast_pass_engine", "nope"))

    def test_range(self):
        self.assertTrue(api_server.setting_valid("idle_sweep_seconds", 60))
        self.assertFalse(api_server.setting_valid("idle_sweep_seconds", 5))    # below min
        self.assertFalse(api_server.setting_valid("idle_sweep_seconds", 9999))  # above max
        self.assertFalse(api_server.setting_valid("idle_sweep_seconds", True))  # bool rejected


class TestFriendly(unittest.TestCase):
    def test_known_code_gets_hint(self):
        self.assertIn("invite the bot", slack._friendly("not_in_channel"))

    def test_unknown_code_passthrough(self):
        self.assertEqual(slack._friendly("weird_error"), "weird_error")


class TestMediaSample(unittest.TestCase):
    def test_downsamples_and_filters_missing(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i in range(50):
                p = os.path.join(d, f"f{i}.jpg")
                open(p, "w").close()
                paths.append(p)
            paths.append(os.path.join(d, "missing.jpg"))  # nonexistent -> filtered out
            out = media._sample(paths, cap=10)
            self.assertEqual(len(out), 10)
            self.assertTrue(all(os.path.exists(p) for p in out))

    def test_under_cap_returns_existing_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.jpg")
            open(p, "w").close()
            self.assertEqual(media._sample([p, "nope.jpg"]), [p])


class TestRecordDelivery(unittest.TestCase):
    def test_records_ok_and_error(self):
        with tempfile.TemporaryDirectory() as d:
            intg.STATE_FILE = os.path.join(d, "state.json")
            intg._record_delivery("slack", "burst", True, "posted clip.gif")
            st = json.load(open(intg.STATE_FILE))
            self.assertTrue(st["slack"]["last_delivery"]["ok"])
            self.assertEqual(st["slack"]["last_delivery"]["kind"], "burst")
            self.assertIn("last_ok", st["slack"])

            intg._record_delivery("slack", "alert", False, "boom")
            st = json.load(open(intg.STATE_FILE))
            self.assertFalse(st["slack"]["last_delivery"]["ok"])
            self.assertIn("last_error", st["slack"])


class TestInferenceMetrics(unittest.TestCase):
    def test_rollup_window_and_split(self):
        with tempfile.TemporaryDirectory() as d:
            now = time.time()
            log = [
                {"started": now, "ok": True, "duration_s": 10, "model": "gemma4:12b"},
                {"started": now, "ok": False, "duration_s": 20, "model": "gemma4:12b"},
                {"started": now, "ok": True, "duration_s": 30, "model": "google/gemma-4-31b-it"},
                {"started": now - 99999, "ok": True, "duration_s": 5, "model": "x"},  # outside window
            ]
            json.dump(log, open(os.path.join(d, "inference_log.json"), "w"))
            orig = api_server.BASE_DIR
            api_server.BASE_DIR = d
            try:
                m = api_server._inference_metrics(window_s=3600)
            finally:
                api_server.BASE_DIR = orig
            self.assertEqual(m["count"], 3)        # the stale 4th is excluded
            self.assertEqual(m["ok"], 2)
            self.assertEqual(m["failures"], 1)
            self.assertEqual(m["cloud"], 1)        # the google/ id
            self.assertEqual(m["local"], 2)


class TestResolveTimezone(unittest.TestCase):
    def test_env_overrides_settings(self):
        self.assertEqual(api_server.resolve_timezone("UTC", "Australia/Sydney"), "UTC")

    def test_settings_used_without_env(self):
        self.assertEqual(api_server.resolve_timezone(None, "Europe/London"), "Europe/London")

    def test_default_when_neither(self):
        self.assertEqual(api_server.resolve_timezone(None, None), "Australia/Sydney")

    def test_blank_values_ignored(self):
        self.assertEqual(api_server.resolve_timezone("", "  "), "Australia/Sydney")
        self.assertEqual(api_server.resolve_timezone("  ", "America/New_York"), "America/New_York")


if __name__ == "__main__":
    unittest.main()
