"""Regression tests for #63 (retention uses the camera clock in the
filename, not mtime) and #69 (configured timezone + DST fold on aging).

Run from the repo root:  python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyze_images


def _fname_stamp(dt):
    """Hikvision-style embed: YYYYMMDDHHMMSS + 3-digit ms."""
    return dt.strftime("%Y%m%d%H%M%S") + "000"


class TestRetentionUsesFilenameClock(unittest.TestCase):
    """#63: age is the filename timestamp; mtime is only a fallback."""

    def setUp(self):
        self._age = analyze_images.MAX_AGE_DAYS
        self._budget = analyze_images.MAX_DIR_GB
        self._persist = analyze_images.PERSIST_BUDGET_PCT
        self._log = analyze_images.RETENTION_LOG
        self._ignore = list(analyze_images.GATE_IGNORE_LABELS)
        self.td = tempfile.TemporaryDirectory()
        self.dir = self.td.name
        analyze_images.MAX_AGE_DAYS = 1
        analyze_images.MAX_DIR_GB = 1000  # no budget pressure
        analyze_images.PERSIST_BUDGET_PCT = 20.0
        analyze_images.RETENTION_LOG = os.path.join(self.dir, "retention_log.json")
        analyze_images.GATE_IGNORE_LABELS = ["car"]

    def tearDown(self):
        analyze_images.MAX_AGE_DAYS = self._age
        analyze_images.MAX_DIR_GB = self._budget
        analyze_images.PERSIST_BUDGET_PCT = self._persist
        analyze_images.RETENTION_LOG = self._log
        analyze_images.GATE_IGNORE_LABELS = self._ignore
        self.td.cleanup()

    def _touch(self, name, mtime, size=100):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(b"x" * size)
        os.utime(path, (mtime, mtime))
        return path

    def test_recent_mtime_old_filename_is_aged_out(self):
        # filename says 3 days old; mtime says "just written". The camera
        # clock must win, so this file is deleted despite the fresh mtime.
        tz = ZoneInfo(analyze_images.configured_tz_name())
        old = "front_01_%s_MOTDEC.jpg" % _fname_stamp(datetime.now(tz) - timedelta(days=3))
        recent = "back_01_%s_MOTDEC.jpg" % _fname_stamp(datetime.now(tz))
        now = time.time()
        self._touch(old, now - 100)          # fresh mtime, old clock
        self._touch(recent, now - 5 * 86400)  # stale mtime, recent clock
        analysis = {old: {"fast_pass": "negative"},
                    recent: {"fast_pass": "negative"}}

        deleted = analyze_images.apply_retention(self.dir, analysis, set())

        self.assertIn(old, deleted)
        self.assertNotIn(recent, deleted)
        self.assertFalse(os.path.exists(os.path.join(self.dir, old)))
        self.assertTrue(os.path.exists(os.path.join(self.dir, recent)))

    def test_mtime_fallback_when_filename_unparseable(self):
        old = "legacy_copy.jpg"
        recent = "legacy_fresh.jpg"
        now = time.time()
        self._touch(old, now - 3 * 86400)
        self._touch(recent, now - 100)
        analysis = {old: {"fast_pass": "negative"},
                    recent: {"fast_pass": "negative"}}

        deleted = analyze_images.apply_retention(self.dir, analysis, set())

        self.assertEqual(deleted, {old})
        self.assertTrue(os.path.exists(os.path.join(self.dir, recent)))

    def test_pinned_and_persistable_ordering_unchanged(self):
        # Pinned never deleted; LLM-persistable survives pass 1 even with a
        # very old camera clock.
        tz = ZoneInfo(analyze_images.configured_tz_name())
        stamp = _fname_stamp(datetime.now(tz) - timedelta(days=9))
        pinned = "front_01_%s_MOTDEC.jpg" % stamp
        llm = "back_01_%s_MOTDEC.jpg" % stamp
        now = time.time()
        self._touch(pinned, now)
        self._touch(llm, now)
        analysis = {llm: {"person": True, "_llm": {"porch_access": True}}}

        deleted = analyze_images.apply_retention(self.dir, analysis, {pinned})

        self.assertNotIn(pinned, deleted)
        self.assertNotIn(llm, deleted)


class TestConfiguredTzAndFold(unittest.TestCase):
    """#69: configured tz is honored and DST fall-back is deterministic."""

    def setUp(self):
        self._s = dict(analyze_images._s)
        self._env = os.environ.pop("WEBCAM_TZ", None)

    def tearDown(self):
        analyze_images._s.clear()
        analyze_images._s.update(self._s)
        if self._env is None:
            os.environ.pop("WEBCAM_TZ", None)
        else:
            os.environ["WEBCAM_TZ"] = self._env

    def test_configured_tz_precedence(self):
        analyze_images._s["timezone"] = "America/New_York"
        self.assertEqual(analyze_images.configured_tz_name(), "America/New_York")
        os.environ["WEBCAM_TZ"] = "UTC"
        self.assertEqual(analyze_images.configured_tz_name(), "UTC")

    def test_tz_name_changes_aging_result(self):
        # Wall clock midnight-ish tomorrow: future in UTC, already past in
        # Sydney (+11), so the same name flips on the configured zone.
        name = "front_01_20260102050000000_MOTDEC.jpg"
        now = datetime(2026, 1, 1, 20, 0, 0, tzinfo=ZoneInfo("UTC"))
        self.assertFalse(
            analyze_images.filename_within_days(name, 1, now=now, tz_name="UTC"))
        self.assertTrue(
            analyze_images.filename_within_days(name, 1, now=now, tz_name="Australia/Sydney"))

    def test_dst_fold_earlier_offset_is_zero(self):
        # Sydney DST ends 2026-04-05 03:00 AEDT -> 02:00 AEST; 02:30 happens
        # twice. Convention: fold=0 = first pass (AEDT, +11, earlier UTC).
        name = "front_01_20260405023000000_MOTDEC.jpg"
        e0 = analyze_images.filename_epoch(name, "Australia/Sydney", fold=0)
        e1 = analyze_images.filename_epoch(name, "Australia/Sydney", fold=1)
        self.assertEqual(e1 - e0, 3600)
        dt0 = analyze_images.filename_datetime(name, "Australia/Sydney", fold=0)
        dt1 = analyze_images.filename_datetime(name, "Australia/Sydney", fold=1)
        self.assertEqual(dt0.utcoffset(), timedelta(hours=11))
        self.assertEqual(dt1.utcoffset(), timedelta(hours=10))
        # Just after the first pass: fold=0 is in-window, fold=1 is future.
        now = datetime(2026, 4, 4, 16, 0, 0, tzinfo=ZoneInfo("UTC"))
        self.assertTrue(analyze_images.filename_within_days(
            name, 1, now=now, tz_name="Australia/Sydney", fold=0))
        self.assertFalse(analyze_images.filename_within_days(
            name, 1, now=now, tz_name="Australia/Sydney", fold=1))

    def test_unambiguous_time_ignores_fold(self):
        name = "front_01_20260820120000000_MOTDEC.jpg"
        self.assertEqual(
            analyze_images.filename_epoch(name, "Australia/Sydney", fold=0),
            analyze_images.filename_epoch(name, "Australia/Sydney", fold=1))


if __name__ == "__main__":
    unittest.main()
