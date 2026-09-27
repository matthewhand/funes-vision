"""#30: every entry path takes the global pipeline lock.

The flock was taken only in the --rescan-days branch, so the cron
--retention-only watchdog ran unlocked: it deleted frames while a live sweep
was mid-cv2.imread on them, and the shared 1s-debounced catalog flush made the
last writer win. The lock now lives in main(), so every caller is serialised.

flock() is per open-file-description, so holding it in this process on its own
fd is a faithful stand-in for "another process holds it".

Run from the repo root:  python3 -m unittest discover -s tests
"""
import fcntl
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


class TestPipelineLock(unittest.TestCase):
    GLOBALS = ("BASE_DIR", "WATCH_DIRS", "_settings_path", "MAX_AGE_DAYS",
               "MAX_DIR_GB", "PERSIST_BUDGET_PCT", "RETENTION_LOG",
               "GATE_IGNORE_LABELS", "PIPELINE_LOCK")

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = self.td.name
        self.img = os.path.join(self.root, "cam1")
        os.makedirs(self.img)
        self.lock_path = os.path.join(self.root, "pipeline.lock")
        # getattr default keeps the behavioural tests runnable (and failing)
        # against a tree whose main() predates the pipeline lock.
        self._saved = {n: getattr(analyze_images, n, None) for n in self.GLOBALS}
        analyze_images.BASE_DIR = self.root
        analyze_images._settings_path = os.path.join(self.root, "settings.json")
        analyze_images.WATCH_DIRS = [self.img]
        analyze_images.MAX_AGE_DAYS = 1
        analyze_images.MAX_DIR_GB = 100
        analyze_images.PERSIST_BUDGET_PCT = 20.0
        analyze_images.RETENTION_LOG = os.path.join(self.root, "retention_log.json")
        analyze_images.GATE_IGNORE_LABELS = ["car"]
        analyze_images.PIPELINE_LOCK = self.lock_path
        with open(os.path.join(self.root, "analysis.json"), "w") as f:
            f.write("{}")
        self.victim = os.path.join(self.img, "old.jpg")
        with open(self.victim, "wb") as f:
            f.write(b"x" * 100)
        stamp = time.time() - 100 * 86400
        os.utime(self.victim, (stamp, stamp))
        with open(analyze_images._settings_path, "w") as f:
            json.dump({"watch_dirs": [self.img], "max_age_days": 1,
                       "max_dir_gb": 100}, f)
        patcher = mock.patch.object(analyze_images, "ollama_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(analyze_images, name, value)
        self.td.cleanup()

    def _hold_lock(self):
        fh = open(self.lock_path, "a")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        self.addCleanup(lambda: (fcntl.flock(fh.fileno(), fcntl.LOCK_UN), fh.close()))
        return fh

    def test_retention_only_does_nothing_while_locked(self):
        self._hold_lock()
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=analyze_images._settings_path)
        log = out.getvalue()
        self.assertIn("Pipeline lock", log)
        self.assertIn("nothing analysed, nothing deleted", log)
        self.assertNotIn("Scanning", log)
        self.assertNotIn("Retention:", log)
        # The whole point: the frame a live sweep might be reading survives.
        self.assertTrue(os.path.exists(self.victim))

    def test_full_sweep_does_nothing_while_locked(self):
        self._hold_lock()
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(settings_path=analyze_images._settings_path)
        self.assertIn("Pipeline lock", out.getvalue())
        self.assertTrue(os.path.exists(self.victim))

    def test_sweep_runs_and_deletes_when_lock_is_free(self):
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=analyze_images._settings_path)
        self.assertNotIn("Pipeline lock", out.getvalue())
        self.assertFalse(os.path.exists(self.victim))

    def test_lock_is_released_after_the_sweep(self):
        analyze_images.main(retention_only=True, settings_path=analyze_images._settings_path)
        # A second run must not be blocked by the first one's lock.
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=analyze_images._settings_path)
        self.assertNotIn("Pipeline lock", out.getvalue())

    def test_lock_is_released_when_the_sweep_raises(self):
        with mock.patch.object(analyze_images, "_run_sweep", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                analyze_images.main(retention_only=True)
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=analyze_images._settings_path)
        self.assertNotIn("Pipeline lock", out.getvalue())

    def test_take_lock_returns_none_when_held(self):
        self._hold_lock()
        self.assertIsNone(analyze_images.take_pipeline_lock(blocking=False))

    def test_take_lock_returns_handle_when_free(self):
        handle = analyze_images.take_pipeline_lock(blocking=False)
        self.assertIsNotNone(handle)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def test_take_lock_creates_the_lock_file(self):
        self.assertFalse(os.path.exists(self.lock_path))
        handle = analyze_images.take_pipeline_lock(blocking=False)
        self.assertTrue(os.path.exists(self.lock_path))
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def test_default_lock_path_is_the_documented_one(self):
        # The CLI (systemd timer / cron) shares one lock with the live sweep.
        # setUp points PIPELINE_LOCK at the throwaway tree, so compare against
        # the module default captured before that override.
        self.assertEqual(self._saved["PIPELINE_LOCK"], "/tmp/webcam_analysis.lock")


if __name__ == "__main__":
    unittest.main()
