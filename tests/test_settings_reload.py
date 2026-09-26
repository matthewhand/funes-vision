"""#77: settings changed at runtime reach the running pipeline.

These exercise the reload path directly: ``apply_settings`` merges a mapping
into the module globals, and ``main`` re-reads settings.json at sweep start so
a value changed via /api/settings lands on the next sweep without a re-import.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyze_images as ai
import scans


class SettingsReloadTest(unittest.TestCase):
    GLOBALS = ("BASE_DIR", "WATCH_DIRS", "_settings_path", "_s",
               "MAX_AGE_DAYS", "MAX_DIR_GB", "BURST_THRESHOLD_SECONDS")

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = self.td.name
        self.img = os.path.join(self.root, "cam1")
        os.makedirs(self.img)
        self.settings_path = os.path.join(self.root, "settings.json")
        self._saved = {name: getattr(ai, name) for name in self.GLOBALS}
        self._saved_scans = scans.MAX_SCANS_PER_IMAGE
        ai.BASE_DIR = self.root
        ai._settings_path = self.settings_path
        ai.WATCH_DIRS = [self.img]
        ollama = mock.patch.object(ai, "ollama_available", return_value=False)
        ollama.start()
        self.addCleanup(ollama.stop)
        self.addCleanup(self._restore)

    def _restore(self):
        for name, value in self._saved.items():
            setattr(ai, name, value)
        scans.MAX_SCANS_PER_IMAGE = self._saved_scans
        self.td.cleanup()

    def _write(self, cfg):
        with open(self.settings_path, "w") as f:
            json.dump(cfg, f)

    def _run(self):
        out = io.StringIO()
        with redirect_stdout(out):
            ai.main(retention_only=True)
        return out.getvalue()

    def test_apply_settings_updates_globals_and_scans(self):
        ai.apply_settings({"max_age_days": 7, "max_scans_per_image": 5,
                           "burst_threshold_seconds": 111})
        self.assertEqual(ai.MAX_AGE_DAYS, 7)
        self.assertEqual(scans.MAX_SCANS_PER_IMAGE, 5)
        self.assertEqual(ai.BURST_THRESHOLD_SECONDS, 111)

    def test_absent_keys_keep_current_values(self):
        ai.apply_settings({"max_age_days": 7})
        before = ai.MAX_DIR_GB
        ai.apply_settings({"max_age_days": 8})
        self.assertEqual(ai.MAX_DIR_GB, before)
        self.assertEqual(ai.MAX_AGE_DAYS, 8)

    def test_sweep_reloads_without_reimport(self):
        module = sys.modules["analyze_images"]
        self._write({"watch_dirs": [self.img], "max_age_days": 2,
                     "max_scans_per_image": 3})
        self._run()
        self.assertEqual(ai.MAX_AGE_DAYS, 2)
        self.assertEqual(scans.MAX_SCANS_PER_IMAGE, 3)

        # Mutate the file the way POST /api/settings does; the very next
        # sweep must observe it on the same, already-imported module.
        self._write({"watch_dirs": [self.img], "max_age_days": 9,
                     "max_scans_per_image": 6})
        self._run()
        self.assertIs(sys.modules["analyze_images"], module)
        self.assertEqual(ai.MAX_AGE_DAYS, 9)
        self.assertEqual(scans.MAX_SCANS_PER_IMAGE, 6)

    def test_corrupt_settings_logged_not_raised(self):
        with open(self.settings_path, "w") as f:
            f.write("{not json")
        with self.assertLogs("analyze_images", level="WARNING") as cm:
            cfg = ai.load_settings()
            ai.apply_settings(cfg)
        self.assertEqual(cfg, {})
        self.assertTrue(any("could not read settings" in m for m in cm.output))


if __name__ == "__main__":
    unittest.main()
