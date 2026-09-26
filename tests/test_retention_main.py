"""End-to-end tests for the retention-only sweep entry point.

These call ``analyze_images.main(retention_only=True)`` on a throwaway tree and
assert the observable retention outcomes (age eviction, pins, detection budget,
backlog eviction). The lower-level ``apply_retention`` matrix already lives in
``test_helpers.py`` and is deliberately not repeated here.
"""
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

import analyze_images


class TestRetentionMainPath(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = self.td.name
        self.img = os.path.join(self.root, "cam1")
        os.makedirs(self.img)
        self._saved = {name: getattr(analyze_images, name) for name in (
            "BASE_DIR", "WATCH_DIRS", "MAX_AGE_DAYS", "MAX_DIR_GB",
            "PERSIST_BUDGET_PCT", "RETENTION_LOG", "GATE_IGNORE_LABELS")}
        analyze_images.BASE_DIR = self.root
        analyze_images.WATCH_DIRS = [self.img]
        analyze_images.RETENTION_LOG = os.path.join(self.root, "retention_log.json")
        analyze_images.PERSIST_BUDGET_PCT = 20.0
        analyze_images.GATE_IGNORE_LABELS = ["car"]
        patcher = mock.patch.object(analyze_images, "ollama_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(analyze_images, name, value)
        self.td.cleanup()

    def _frame(self, name, size, age_days):
        path = os.path.join(self.img, name)
        with open(path, "wb") as f:
            f.write(b"x" * size)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def _state(self, analysis, pins):
        with open(os.path.join(self.root, "analysis.json"), "w") as f:
            json.dump(analysis, f)
        with open(os.path.join(self.root, "pins.json"), "w") as f:
            json.dump(pins, f)

    def _run(self):
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True)
        return out.getvalue()

    def test_age_pass_removes_analyzed_empty_but_keeps_pins_and_recent(self):
        analyze_images.MAX_AGE_DAYS = 1
        analyze_images.MAX_DIR_GB = 100
        self._frame("old_empty.jpg", 100, 3)
        self._frame("pinned.jpg", 100, 3)
        self._frame("recent_det.jpg", 100, 0.01)
        self._state({"old_empty.jpg": {"fast_pass": "negative"},
                     "pinned.jpg": {"fast_pass": "negative"},
                     "recent_det.jpg": {"person": True}}, ["pinned.jpg"])

        log = self._run()

        self.assertIn("Retention-only: skipped analysis", log)
        self.assertFalse(os.path.exists(os.path.join(self.img, "old_empty.jpg")))
        self.assertTrue(os.path.exists(os.path.join(self.img, "pinned.jpg")))
        self.assertTrue(os.path.exists(os.path.join(self.img, "recent_det.jpg")))

    def test_detection_survives_within_budget_but_evicts_when_over(self):
        analyze_images.MAX_AGE_DAYS = 3650
        analyze_images.MAX_DIR_GB = 500 / (1024 ** 3)
        self._frame("n1.jpg", 400, 1)
        self._frame("d1.jpg", 400, 0.5)
        self._state({"n1.jpg": {"fast_pass": "negative"}, "d1.jpg": {"person": True}}, [])
        self._run()
        self.assertFalse(os.path.exists(os.path.join(self.img, "n1.jpg")))
        self.assertTrue(os.path.exists(os.path.join(self.img, "d1.jpg")))

    def test_detection_evicted_only_after_budget_unavoidable(self):
        analyze_images.MAX_AGE_DAYS = 3650
        analyze_images.MAX_DIR_GB = 500 / (1024 ** 3)
        self._frame("n1.jpg", 400, 1)
        self._frame("d1.jpg", 400, 0.5)
        self._frame("d2.jpg", 400, 0.4)
        self._state({"n1.jpg": {"fast_pass": "negative"},
                     "d1.jpg": {"person": True},
                     "d2.jpg": {"dog": True}}, [])
        self._run()
        self.assertFalse(os.path.exists(os.path.join(self.img, "n1.jpg")))
        surviving = [f for f in ("d1.jpg", "d2.jpg")
                     if os.path.exists(os.path.join(self.img, f))]
        self.assertEqual(len(surviving), 1)
        total = sum(os.path.getsize(os.path.join(self.img, f)) for f in surviving)
        self.assertLessEqual(total, 500)

    def test_backlog_retained_when_within_budget(self):
        analyze_images.MAX_AGE_DAYS = 3650
        analyze_images.MAX_DIR_GB = 1
        self._frame("backlog_old.jpg", 100, 100)
        self._frame("backlog_new.jpg", 100, 0.1)
        self._state({}, [])
        self._run()
        self.assertTrue(os.path.exists(os.path.join(self.img, "backlog_old.jpg")))
        self.assertTrue(os.path.exists(os.path.join(self.img, "backlog_new.jpg")))

    def test_backlog_evicted_only_when_over_budget(self):
        analyze_images.MAX_AGE_DAYS = 3650
        analyze_images.MAX_DIR_GB = 500 / (1024 ** 3)
        self._frame("known.jpg", 200, 1)
        self._frame("backlog_old.jpg", 400, 100)
        self._frame("backlog_new.jpg", 400, 0.1)
        self._state({"known.jpg": {"fast_pass": "negative"}}, [])
        self._run()
        self.assertFalse(os.path.exists(os.path.join(self.img, "known.jpg")))
        self.assertFalse(os.path.exists(os.path.join(self.img, "backlog_old.jpg")))
        self.assertTrue(os.path.exists(os.path.join(self.img, "backlog_new.jpg")))

    def test_retention_only_writes_no_analysis_rows(self):
        analyze_images.MAX_AGE_DAYS = 3650
        analyze_images.MAX_DIR_GB = 100
        self._frame("fresh.jpg", 100, 0.01)
        self._state({}, [])
        self._run()
        with open(os.path.join(self.root, "analysis.json")) as f:
            self.assertEqual(json.load(f), {})


if __name__ == "__main__":
    unittest.main()
