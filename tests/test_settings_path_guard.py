"""Regression guard: a sweep must be steerable by settings path, and only by it.

``main()`` re-reads settings at the start of every sweep, which is right in
production and a trap in tests: without a seam, any test that pokes the module
globals can have them clobbered by whichever ``settings.json`` sits next to the
script -- which is why CI (no such file) and a developer box behaved differently.

Both tests redirect ``_settings_path`` at a throwaway file rather than the
repo's real one, so they hold on any machine:

* the first proves an explicit ``settings_path`` wins over the module default;
* the second proves the default is still the module default, so a deploy that
  edits ``settings.json`` keeps working.

The same reasoning applies to ``PIPELINE_LOCK``, the other machine-global the
sweep reads: it is redirected into the throwaway tree too, so a live sweep on
the developer's box cannot turn "the frame survived" into an answer about the
lock instead of about the settings path.
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hermetic

import analyze_images


class SettingsPathGuardTest(unittest.TestCase):
    GLOBALS = ("BASE_DIR", "WATCH_DIRS", "_settings_path", "MAX_AGE_DAYS",
               "MAX_DIR_GB", "RETENTION_LOG", "GATE_IGNORE_LABELS")

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = self.td.name
        self.img = os.path.join(self.root, "cam1")
        os.makedirs(self.img)
        self.deployed = os.path.join(self.root, "deployed_settings.json")
        self.chosen = os.path.join(self.root, "chosen_settings.json")
        self._saved = {name: getattr(analyze_images, name) for name in self.GLOBALS}
        analyze_images.BASE_DIR = self.root
        analyze_images.WATCH_DIRS = [self.img]
        analyze_images.RETENTION_LOG = os.path.join(self.root, "retention_log.json")
        analyze_images.GATE_IGNORE_LABELS = ["car"]
        # Stand in for the box's deployed settings.json.
        analyze_images._settings_path = self.deployed
        # The other half of the same trap as settings.json: main() takes the
        # machine-global /tmp/webcam_analysis.lock non-blocking and returns a
        # benign exit 0 when it is busy. On a box with a sweep running, both
        # tests below then "passed" or failed on a sweep that never ran.
        hermetic.pin_private_lock(self, analyze_images, self.root)
        patcher = mock.patch.object(analyze_images, "ollama_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._restore)

    def _restore(self):
        for name, value in self._saved.items():
            setattr(analyze_images, name, value)
        self.td.cleanup()

    def _write(self, path, **cfg):
        cfg = {"watch_dirs": [self.img], **cfg}
        with open(path, "w") as f:
            json.dump(cfg, f)

    def _sweep(self, **kwargs):
        # One analyzed, unpinned, 3-day-old frame: evicted only if the sweep
        # actually ran with a 1-day age budget.
        path = os.path.join(self.img, "stale.jpg")
        with open(path, "wb") as f:
            f.write(b"x" * 100)
        stamp = time.time() - 3 * 86400
        os.utime(path, (stamp, stamp))
        with open(os.path.join(self.root, "analysis.json"), "w") as f:
            json.dump({"stale.jpg": {"fast_pass": "negative"}}, f)
        with open(os.path.join(self.root, "pins.json"), "w") as f:
            json.dump([], f)
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, **kwargs)
        # A busy global lock or an unconfigured sweep also exits 0, so `stale.jpg`
        # surviving would otherwise not mean what the caller thinks it means.
        hermetic.assert_sweep_ran(self, out.getvalue())
        return os.path.exists(path)

    def test_chosen_path_wins_over_deployed_settings(self):
        # Deployed settings would keep everything: a 10-year age budget.
        self._write(self.deployed, max_age_days=3650, max_dir_gb=100)
        self._write(self.chosen, max_age_days=1, max_dir_gb=100)

        survived = self._sweep(settings_path=self.chosen)

        self.assertFalse(survived, "sweep followed the deployed settings.json, not settings_path")
        self.assertEqual(analyze_images.MAX_AGE_DAYS, 1)

    def test_sweep_without_path_still_reads_deployed_settings(self):
        # Production default: no settings_path means the module-level file.
        self._write(self.deployed, max_age_days=1, max_dir_gb=100)

        survived = self._sweep()

        self.assertFalse(survived, "sweep ignored the deployed settings.json")
        self.assertEqual(analyze_images.MAX_AGE_DAYS, 1)


if __name__ == "__main__":
    unittest.main()
