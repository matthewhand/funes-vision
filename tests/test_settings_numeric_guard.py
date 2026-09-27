"""#28: a hostile settings.json must not kill the sweep (or the import).

settings.json is user-writable via /api/settings and survives restarts.
``multi_image_2h_minutes`` used to be coerced with an unguarded float() and
``max_dir_gb`` was taken raw, so a null died inside the module-level
apply_settings() (rc=1 at import, no output at all) and a "5.0" string started
the sweep then died at the budget multiplication. Either way retention, catalog
pruning and the watchdog retention path never ran and the disk grew unbounded.

Every numeric key now goes through one guard that logs and keeps the previous
value, mirroring the max_scans_per_image pattern.

Run from the repo root:  python3 -m unittest discover -s tests
"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyze_images

HOSTILE = {
    "multi_image_2h_minutes": None,      # died at import: float(None)
    "multi_image_3h_minutes": "abc",      # died at import: float('abc')
    "max_age_days": None,                 # died mid-sweep: None * 86400
    "persist_budget_pct": {},             # died mid-sweep: float({})
    "min_mem_for_local_gb": [1],          # died at local_mem_threshold()
    "burst_threshold_seconds": True,      # bool is not a threshold
    "max_deep_passes": "many",
    "deep_concurrency": None,
    "max_scans_per_image": None,
    "max_dir_gb": "5.0",                  # valid number: coerced, not rejected
}


class TestSettingsNumericGuard(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = self.td.name
        self.img = os.path.join(self.root, "cam1")
        os.makedirs(self.img)
        with open(os.path.join(self.img, "f.jpg"), "wb") as f:
            f.write(b"x")
        self.settings_path = os.path.join(self.root, "settings.json")
        # getattr default so the behavioural assertions still run (and fail
        # loudly) against a tree whose main() predates the pipeline lock.
        self._saved = {n: getattr(analyze_images, n, None) for n in (
            "BASE_DIR", "_settings_path", "WATCH_DIRS", "MAX_AGE_DAYS", "MAX_DIR_GB",
            "PERSIST_BUDGET_PCT", "BURST_THRESHOLD_SECONDS", "MULTI_IMAGE_2H",
            "MULTI_IMAGE_3H", "MIN_MEM_FOR_LOCAL_GB", "MAX_DEEP_PASSES",
            "DEEP_CONCURRENCY", "RETENTION_LOG", "PIPELINE_LOCK", "GATE_IGNORE_LABELS")}
        analyze_images.BASE_DIR = self.root
        analyze_images._settings_path = self.settings_path
        analyze_images.WATCH_DIRS = [self.img]
        analyze_images.RETENTION_LOG = os.path.join(self.root, "retention_log.json")
        analyze_images.PIPELINE_LOCK = os.path.join(self.root, "pipeline.lock")
        patcher = mock.patch.object(analyze_images, "ollama_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(analyze_images, name, value)
        self.td.cleanup()

    def test_apply_settings_never_raises_and_keeps_prior_values(self):
        # max_dir_gb is deliberately absent from HOSTILE: "5.0" is a *valid*
        # number that must be coerced, and has its own test below.
        before = {k: getattr(analyze_images, k) for k in
                  ("MAX_AGE_DAYS", "PERSIST_BUDGET_PCT",
                   "BURST_THRESHOLD_SECONDS", "MULTI_IMAGE_2H", "MULTI_IMAGE_3H",
                   "MIN_MEM_FOR_LOCAL_GB", "MAX_DEEP_PASSES", "DEEP_CONCURRENCY")}
        with self.assertLogs("analyze_images", level="WARNING") as cm:
            analyze_images.apply_settings(dict(HOSTILE))
        for key, value in before.items():
            self.assertEqual(getattr(analyze_images, key), value, key)
        logged = "\n".join(cm.output)
        for key in ("multi_image_2h_minutes", "multi_image_3h_minutes",
                    "max_age_days", "persist_budget_pct", "burst_threshold_seconds",
                    "min_mem_for_local_gb", "max_deep_passes", "deep_concurrency"):
            self.assertIn(key, logged, key)

    def test_apply_settings_keeps_prior_budget_for_junk_values(self):
        analyze_images.apply_settings({"max_dir_gb": 3.0})
        with self.assertLogs("analyze_images", level="WARNING") as cm:
            analyze_images.apply_settings({"max_dir_gb": "five gigs"})
        self.assertEqual(analyze_images.MAX_DIR_GB, 3.0)
        self.assertIn("max_dir_gb", "\n".join(cm.output))

    def test_numeric_string_max_dir_gb_is_coerced_not_rejected(self):
        # "5.0" is a valid number: it used to survive settings load and then
        # die at `MAX_DIR_GB * 1024**3`. It must land as a real float.
        analyze_images.apply_settings({"max_dir_gb": "5.0"})
        self.assertEqual(analyze_images.MAX_DIR_GB, 5.0)

    def test_numeric_string_multi_image_minutes_coerced(self):
        analyze_images.apply_settings({"multi_image_2h_minutes": "12.5",
                                       "multi_image_3h_minutes": 7})
        self.assertEqual(analyze_images.MULTI_IMAGE_2H, 12.5)
        self.assertEqual(analyze_images.MULTI_IMAGE_3H, 7.0)

    def test_non_finite_and_bool_budgets_keep_prior(self):
        analyze_images.apply_settings({"max_dir_gb": 3.0})
        for bad in (float("inf"), float("nan"), True, [1], {"a": 1}):
            with self.subTest(budget=bad):
                analyze_images.apply_settings({"max_dir_gb": bad})
                self.assertEqual(analyze_images.MAX_DIR_GB, 3.0)

    def test_out_of_range_values_are_clamped(self):
        analyze_images.apply_settings({"persist_budget_pct": 250,
                                       "max_dir_gb": -4,
                                       "max_age_days": -1,
                                       "burst_threshold_seconds": -5})
        self.assertEqual(analyze_images.PERSIST_BUDGET_PCT, 100.0)
        self.assertEqual(analyze_images.MAX_DIR_GB, 0)
        self.assertEqual(analyze_images.MAX_AGE_DAYS, 0)
        self.assertEqual(analyze_images.BURST_THRESHOLD_SECONDS, 0)

    def test_valid_values_still_apply(self):
        analyze_images.apply_settings({
            "watch_dirs": [self.img], "max_age_days": 7, "max_dir_gb": 2.5,
            "persist_budget_pct": 10, "burst_threshold_seconds": 60,
            "multi_image_2h_minutes": 3, "multi_image_3h_minutes": 9,
            "min_mem_for_local_gb": 8, "max_deep_passes": 5, "deep_concurrency": 2,
        })
        self.assertEqual(analyze_images.MAX_AGE_DAYS, 7)
        self.assertEqual(analyze_images.MAX_DIR_GB, 2.5)
        self.assertEqual(analyze_images.PERSIST_BUDGET_PCT, 10)
        self.assertEqual(analyze_images.BURST_THRESHOLD_SECONDS, 60)
        self.assertEqual(analyze_images.MULTI_IMAGE_2H, 3)
        self.assertEqual(analyze_images.MULTI_IMAGE_3H, 9)
        self.assertEqual(analyze_images.MIN_MEM_FOR_LOCAL_GB, 8)
        self.assertEqual(analyze_images.MAX_DEEP_PASSES, 5)
        self.assertEqual(analyze_images.DEEP_CONCURRENCY, 2)

    def test_sweep_completes_with_hostile_settings(self):
        """End to end: the cron watchdog must still run retention."""
        with open(self.settings_path, "w") as f:
            json.dump({"watch_dirs": [self.img], **HOSTILE}, f)
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=self.settings_path)
        log = out.getvalue()
        self.assertIn("Retention-only: skipped analysis", log)
        self.assertNotIn("Traceback", log)

    def test_sweep_completes_with_null_multi_image_minutes(self):
        """The exact repro: null used to raise TypeError during the import-time
        apply_settings(), so the process died with rc=1 and no output."""
        with open(self.settings_path, "w") as f:
            json.dump({"watch_dirs": [self.img],
                       "multi_image_2h_minutes": None}, f)
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=self.settings_path)
        self.assertIn("Retention-only: skipped analysis", out.getvalue())

    def test_sweep_still_deletes_with_hostile_settings(self):
        """Fail-closed on the value, not on the whole retention path: an old
        frame is still aged out, so the disk is still bounded."""
        old = os.path.join(self.img, "old.jpg")
        with open(old, "wb") as f:
            f.write(b"x")
        stamp = time.time() - 100 * 86400
        os.utime(old, (stamp, stamp))
        with open(self.settings_path, "w") as f:
            json.dump({"watch_dirs": [self.img], **HOSTILE,
                       "max_age_days": 1, "max_dir_gb": 1000}, f)
        with redirect_stdout(io.StringIO()):
            analyze_images.main(retention_only=True, settings_path=self.settings_path)
        self.assertFalse(os.path.exists(old))


if __name__ == "__main__":
    unittest.main()
