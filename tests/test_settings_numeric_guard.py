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
        # max_dir_gb and burst_threshold_seconds still clamp to 0: that is the
        # *safe* end of both ("no disk budget", "no burst grouping").
        #
        # max_age_days and persist_budget_pct used to clamp to 0 as well, which
        # is the destructive end of both: 0 means "every non-persistable frame
        # is expired" and "the persist archive may occupy nothing", so a single
        # mistyped -1 deleted verified frames while logging a line that read
        # like a guard (#54). They now floor at 1. This assertion changed with
        # that fix; it asserted the old, data-losing behaviour.
        analyze_images.apply_settings({"persist_budget_pct": 250,
                                       "max_dir_gb": -4,
                                       "max_age_days": -1,
                                       "burst_threshold_seconds": -5})
        self.assertEqual(analyze_images.PERSIST_BUDGET_PCT, 100.0)
        self.assertEqual(analyze_images.MAX_DIR_GB, 0)
        self.assertEqual(analyze_images.MAX_AGE_DAYS, 1)
        self.assertEqual(analyze_images.BURST_THRESHOLD_SECONDS, 0)

    def test_destructive_knobs_never_clamp_to_their_destructive_end(self):
        """#54: lo=0.0/lo=0 is only a guard for knobs whose low end is safe.

        max_age_days: 0 expires every non-persistable frame the instant the
        sweep starts; persist_budget_pct: 0 caps the persist archive at
        nothing, so every age-expired LLM timeline frame goes; and 0 deep
        passes / 0 concurrency starve (or break) the rebuild that is supposed
        to release the #56 protection. All four now floor at 1.
        """
        analyze_images.apply_settings({"max_age_days": -1, "max_dir_gb": 0})
        self.assertEqual(analyze_images.MAX_AGE_DAYS, 1)
        analyze_images.apply_settings({"persist_budget_pct": -1, "max_dir_gb": 5})
        self.assertEqual(analyze_images.PERSIST_BUDGET_PCT, 1)
        self.assertEqual(analyze_images.MAX_DIR_GB, 5)
        analyze_images.apply_settings({"max_deep_passes": -1,
                                       "deep_concurrency": -1})
        self.assertEqual(analyze_images.MAX_DEEP_PASSES, 1)
        self.assertEqual(analyze_images.DEEP_CONCURRENCY, 1)

    def test_deep_pass_knobs_accept_an_integral_float_string(self):
        """#58: the int knobs now tolerate "5.0" like the float ones, because a
        float-typed settings field or a hand-edited ".0" was rejected outright
        and silently kept the previous value. A *fractional* value is refused
        rather than truncated."""
        analyze_images.apply_settings({"max_deep_passes": "5.0",
                                       "deep_concurrency": 2.0})
        self.assertEqual(analyze_images.MAX_DEEP_PASSES, 5)
        self.assertEqual(analyze_images.DEEP_CONCURRENCY, 2)
        with self.assertLogs("analyze_images", level="WARNING"):
            analyze_images.apply_settings({"max_deep_passes": 5.5})
        self.assertEqual(analyze_images.MAX_DEEP_PASSES, 5)
        with self.assertLogs("analyze_images", level="WARNING"):
            analyze_images.apply_settings({"deep_concurrency": "nope"})
        self.assertEqual(analyze_images.DEEP_CONCURRENCY, 2)

    def test_list_settings_guard(self):
        """#58: non-numeric keys go through setting_list()/setting_str()."""
        analyze_images.apply_settings({"watch_dirs": [self.img],
                                       "gate_ignore_labels": ["car", "dog"],
                                       "cameras": [{"id": "cam1"}],
                                       "ignore_regions": [{"label": "car"}]})
        self.assertEqual(analyze_images.WATCH_DIRS, [self.img])
        self.assertEqual(list(analyze_images.GATE_IGNORE_LABELS), ["car", "dog"])
        self.assertEqual(analyze_images.CAMERAS, [{"id": "cam1"}])
        self.assertEqual(analyze_images.IGNORE_REGIONS, [{"label": "car"}])
        # A bare scalar is the obvious intent: wrapped, never exploded into one
        # element per character the way set("car") == {"c","a","r"} did.
        with self.assertLogs("analyze_images", level="WARNING") as cm:
            analyze_images.apply_settings({"gate_ignore_labels": "car"})
        self.assertEqual(list(analyze_images.GATE_IGNORE_LABELS), ["car"])
        self.assertIn("gate_ignore_labels", "\n".join(cm.output))
        # Uncoercible shapes keep the previous list rather than exploding.
        with self.assertLogs("analyze_images", level="WARNING"):
            analyze_images.apply_settings({"gate_ignore_labels": {"a": 1}})
        self.assertEqual(list(analyze_images.GATE_IGNORE_LABELS), ["car"])
        with self.assertLogs("analyze_images", level="WARNING"):
            analyze_images.apply_settings({"watch_dirs": 7})
        self.assertEqual(analyze_images.WATCH_DIRS, [self.img])
        with self.assertLogs("analyze_images", level="WARNING"):
            analyze_images.apply_settings({"cameras": True})
        self.assertEqual(analyze_images.CAMERAS, [{"id": "cam1"}])
        # An explicit null/[] still clears, as the callers' `or []` did.
        analyze_images.apply_settings({"cameras": []})
        self.assertEqual(analyze_images.CAMERAS, [])
        analyze_images.apply_settings({"cameras": None})
        self.assertEqual(analyze_images.CAMERAS, [])

    def test_ollama_url_scalar_guard(self):
        """#58: ollama_url is a scalar, not a list, so it gets setting_str()."""
        analyze_images.apply_settings({"ollama_url": "http://box:11434"})
        self.assertEqual(analyze_images.OLLAMA_URL, "http://box:11434")
        for bad in (["http://box:11434"], {"url": 1}, "", "   "):
            with self.subTest(bad=bad):
                with self.assertLogs("analyze_images", level="WARNING") as cm:
                    analyze_images.apply_settings({"ollama_url": bad})
                self.assertEqual(analyze_images.OLLAMA_URL, "http://box:11434")
                self.assertIn("ollama_url", "\n".join(cm.output))

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
        frame is still aged out, so the disk is still bounded.

        The frame carries a catalog row on purpose. With no catalog at all, an
        uncatalogued frame is now exempt from retention while the catalog
        cannot account for it (#56) -- so this would have been asserting the
        data-loss path the fix removed, not the retention path this test
        covers.
        """
        old = os.path.join(self.img, "old.jpg")
        with open(old, "wb") as f:
            f.write(b"x")
        stamp = time.time() - 100 * 86400
        os.utime(old, (stamp, stamp))
        with open(os.path.join(self.root, "analysis.json"), "w") as f:
            json.dump({"old.jpg": {"fast_pass": "negative"}}, f)
        with open(self.settings_path, "w") as f:
            json.dump({"watch_dirs": [self.img], **HOSTILE,
                       "max_age_days": 1, "max_dir_gb": 1000}, f)
        with redirect_stdout(io.StringIO()):
            analyze_images.main(retention_only=True, settings_path=self.settings_path)
        self.assertFalse(os.path.exists(old))


if __name__ == "__main__":
    unittest.main()
