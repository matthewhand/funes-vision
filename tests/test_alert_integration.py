"""Integration-style tests for run_health_checks alert rate limiting.

Mocks Slack (notify_alert), disk scanning, and time so we can drive the
full state machine without network or real cameras.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyze_images as ai


class AlertIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, "alert_state.json")
        self.calls = []  # list of (msg, update_ts)
        self._ts_counter = 1000.0

        def fake_notify(msg, update_ts=None):
            self.calls.append((msg, update_ts))
            # Return a stable-ish ts; if updating, keep the same ts
            if update_ts:
                return True, update_ts
            self._ts_counter += 1
            return True, str(self._ts_counter)

        # Force a single camera dir that exists
        self.cam_dir = os.path.join(self.tmp.name, "Webcam22")
        os.makedirs(self.cam_dir, exist_ok=True)

        self.patches = [
            mock.patch.object(ai, "ALERT_STATE", self.state_path),
            mock.patch.object(ai, "DEEP_PASSES_ENABLED", False),  # no llm_down
            mock.patch.object(ai, "INFERENCE_LOG", os.path.join(self.tmp.name, "empty.json")),
            mock.patch("integrations.notify_alert", side_effect=fake_notify),
        ]
        for p in self.patches:
            p.start()

        # Default: disk over budget, frames recent (no cam offline)
        self.scan_total = int(ai.MAX_DIR_GB * 1024 ** 3 * 0.95)  # 95%
        self.scan_newest = time.time()  # recent
        self.scan_patch = mock.patch.object(
            ai, "_scan_dir",
            side_effect=lambda d: (self.scan_newest, self.scan_total),
        )
        self.scan_patch.start()

        # Controllable clock
        self.now = 1_700_000_000.0
        self.time_patch = mock.patch.object(ai.time, "time", side_effect=lambda: self.now)
        self.time_patch.start()

    def tearDown(self):
        self.time_patch.stop()
        self.scan_patch.stop()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _run(self):
        ai.run_health_checks([self.cam_dir], api_key=None)

    def _state(self):
        if not os.path.exists(self.state_path):
            return {}
        with open(self.state_path) as f:
            return json.load(f)

    def _advance(self, seconds):
        self.now += seconds

    # --- tests ---

    def test_first_disk_alert_posts_once(self):
        self._run()
        self.assertEqual(len(self.calls), 1)
        msg, update_ts = self.calls[0]
        self.assertIsNone(update_ts)  # fresh post
        self.assertIn("Webcam22", msg)
        self.assertIn("retention is deleting images", msg)
        self.assertNotIn("×", msg)  # count 1 → no suffix

        st = self._state()
        self.assertTrue(st["disk_Webcam22"]["active"])
        self.assertEqual(st["disk_Webcam22"]["fire_count"], 1)
        self.assertTrue(st["disk_Webcam22"]["slack_ts"])
        self.assertIn("Webcam22", st["disk_Webcam22"]["last_text"])

    def test_identical_within_interval_no_extra_send(self):
        self._run()
        self.assertEqual(len(self.calls), 1)
        # Immediate second sweep — still inside 10 min identical window
        self._advance(60)
        self._run()
        self.assertEqual(len(self.calls), 1)  # no new call

    def test_identical_after_short_interval_updates_with_count(self):
        self._run()
        stored_ts = self._state()["disk_Webcam22"]["slack_ts"]

        self._advance(ai.IDENTICAL_UPDATE_S + 1)
        self._run()

        self.assertEqual(len(self.calls), 2)
        msg2, update_ts2 = self.calls[1]
        self.assertEqual(update_ts2, stored_ts)  # chat.update on same ts
        self.assertIn("×2", msg2)
        self.assertEqual(self._state()["disk_Webcam22"]["fire_count"], 2)

    def test_identical_counter_keeps_climbing(self):
        self._run()
        stored_ts = self._state()["disk_Webcam22"]["slack_ts"]
        for expected in (2, 3, 4):
            self._advance(ai.IDENTICAL_UPDATE_S + 1)
            self._run()
            self.assertIn(f"×{expected}", self.calls[-1][0])
            self.assertEqual(self.calls[-1][1], stored_ts)
            self.assertEqual(self._state()["disk_Webcam22"]["fire_count"], expected)

    def test_global_max_queue_caps_sends(self):
        # Force several distinct alert keys by using multiple dirs
        dirs = []
        for name in ("CamA", "CamB", "CamC", "CamD", "CamE", "CamF"):
            d = os.path.join(self.tmp.name, name)
            os.makedirs(d, exist_ok=True)
            dirs.append(d)

        self.calls.clear()
        # Reset state file
        if os.path.exists(self.state_path):
            os.remove(self.state_path)

        ai.run_health_checks(dirs, api_key=None)
        # 6 cameras over budget → would be 6 alerts, but global cap is 4
        self.assertLessEqual(len(self.calls), ai.MAX_ALERTS_PER_WINDOW)
        self.assertEqual(len(self.calls), ai.MAX_ALERTS_PER_WINDOW)

    def test_recovery_when_disk_clears(self):
        self._run()
        self.assertEqual(len(self.calls), 1)
        stored_ts = self._state()["disk_Webcam22"]["slack_ts"]

        # Drop under budget and advance past recovery cooldowns
        self.scan_total = int(ai.MAX_DIR_GB * 1024 ** 3 * 0.5)  # 50%
        self._advance(max(ai.RECOVERY_COOLDOWN_S, 601) + 1)
        self._run()

        # Last call should be a recovery message updating the original ts
        self.assertGreaterEqual(len(self.calls), 2)
        recovery_msg, recovery_ts = self.calls[-1]
        self.assertIn("back under budget", recovery_msg)
        self.assertEqual(recovery_ts, stored_ts)

        st = self._state()["disk_Webcam22"]
        self.assertFalse(st["active"])
        self.assertEqual(st["fire_count"], 0)
        self.assertEqual(st.get("last_text", ""), "")

    def test_no_spam_on_rapid_flap(self):
        """Alert → brief recovery attempt inside cooldown → alert again.
        Should not produce recovery spam or extra posts."""
        self._run()
        self.assertEqual(len(self.calls), 1)

        # Briefly under budget but recovery cooldown not met
        self.scan_total = int(ai.MAX_DIR_GB * 1024 ** 3 * 0.5)
        self._advance(30)  # far less than RECOVERY_COOLDOWN_S
        self._run()
        # No recovery send yet
        self.assertEqual(len(self.calls), 1)

        # Back over budget quickly
        self.scan_total = int(ai.MAX_DIR_GB * 1024 ** 3 * 0.95)
        self._advance(30)
        self._run()
        # Still only the original post (identical path blocked by short interval
        # or active state without due)
        self.assertEqual(len(self.calls), 1)

    def test_missing_offline_hours_key_uses_24_not_12(self):
        settings_path = os.path.join(self.tmp.name, "settings.json")
        with open(settings_path, "w") as f:
            json.dump({"max_dir_gb": 5}, f)
        self.scan_total = 100  # under budget — only camera-offline can fire
        self.scan_newest = self.now - 13 * 3600
        with mock.patch.object(ai, "_settings_path", settings_path):
            self._run()
            self.assertEqual(self.calls, [])  # 13h is past 12h, not past 24h
            self.scan_newest = self.now - 25 * 3600
            self._run()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("camera offline", self.calls[0][0])


if __name__ == "__main__":
    unittest.main()
