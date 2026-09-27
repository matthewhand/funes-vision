"""#23: an absent watch dir must postpone the catalog prune, never commit it.

A watch dir that is momentarily missing (unmounted volume, NFS blip, a bind
mount that is not up yet) makes every one of its frames look deleted: the
per-dir scan tolerates it (`continue`), but the prune built `existing` from the
dirs that *do* exist and committed analysis.json as {} — destroying all HA
flags, LLM summaries and burst links for that camera, permanently. The burst
prune did the same.

Run from the repo root:  python3 -m unittest discover -s tests
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

import analyze_images


class TestCatalogPruneAbsentDir(unittest.TestCase):
    GLOBALS = ("BASE_DIR", "WATCH_DIRS", "_settings_path", "MAX_AGE_DAYS",
               "MAX_DIR_GB", "PERSIST_BUDGET_PCT", "RETENTION_LOG",
               "GATE_IGNORE_LABELS", "PIPELINE_LOCK")

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = self.td.name
        self.live = os.path.join(self.root, "cam_live")
        self.absent = os.path.join(self.root, "cam_absent")
        os.makedirs(self.live)
        os.makedirs(self.absent)
        # getattr default so the behavioural assertions still run (and fail
        # loudly) against a tree whose main() predates the pipeline lock.
        self._saved = {n: getattr(analyze_images, n, None) for n in self.GLOBALS}
        analyze_images.BASE_DIR = self.root
        analyze_images._settings_path = os.path.join(self.root, "settings.json")
        analyze_images.WATCH_DIRS = [self.live, self.absent]
        analyze_images.MAX_AGE_DAYS = 3650        # keep every frame; isolate the prune
        analyze_images.MAX_DIR_GB = 1000
        analyze_images.PERSIST_BUDGET_PCT = 20.0
        analyze_images.RETENTION_LOG = os.path.join(self.root, "retention_log.json")
        analyze_images.GATE_IGNORE_LABELS = ["car"]
        analyze_images.PIPELINE_LOCK = os.path.join(self.root, "pipeline.lock")
        self.analysis_file = os.path.join(self.root, "analysis.json")
        self.burst_file = os.path.join(self.root, "bursts.json")
        patcher = mock.patch.object(analyze_images, "ollama_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(analyze_images, name, value)
        self.td.cleanup()

    def _frame(self, directory, name):
        with open(os.path.join(directory, name), "wb") as f:
            f.write(b"x")

    def _seed(self, n_live=40, n_absent=40):
        rows = {}
        for i in range(n_live):
            name = f"live{i:02d}.jpg"
            self._frame(self.live, name)
            rows[name] = {"person": True, "_llm": {"porch_access": True}}
        for i in range(n_absent):
            name = f"away{i:02d}.jpg"
            self._frame(self.absent, name)
            rows[name] = {"person": True, "_llm": {"porch_access": True}}
        with open(self.analysis_file, "w") as f:
            json.dump(rows, f, indent=2)
        with open(self.burst_file, "w") as f:
            json.dump({"b1": {"summary": "s", "images": ["live00.jpg", "live01.jpg"]}},
                      f, indent=2)
        return rows

    def _sweep(self):
        settings = os.path.join(self.root, "settings.json")
        with open(settings, "w") as f:
            json.dump({"watch_dirs": [self.live, self.absent],
                       "max_age_days": 3650, "max_dir_gb": 1000}, f)
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=settings)
        return out.getvalue()

    def _analysis(self):
        with open(self.analysis_file) as f:
            return json.load(f)

    def test_absent_dir_keeps_every_row(self):
        rows = self._seed()
        os.rename(self.absent, self.absent + ".moved")   # volume went away

        log = self._sweep()

        self.assertIn("catalog prune postponed", log)
        self.assertNotIn("Pruned", log)
        after = self._analysis()
        self.assertEqual(len(after), len(rows))
        self.assertTrue(after)                            # not {} — the data-loss shape
        # The dir that IS present keeps its own rows...
        self.assertIn("live00.jpg", after)
        self.assertEqual(after["live00.jpg"]["_llm"], {"porch_access": True})
        # ...and the absent dir's rows survive too, verbatim.
        self.assertEqual(after["away39.jpg"], rows["away39.jpg"])

    def test_absent_dir_keeps_bursts(self):
        self._seed()
        os.rename(self.absent, self.absent + ".moved")
        self._sweep()
        with open(self.burst_file) as f:
            self.assertEqual(set(json.load(f)), {"b1"})

    def test_prune_still_runs_when_every_dir_is_present(self):
        rows = self._seed()
        os.remove(os.path.join(self.live, "live00.jpg"))
        os.remove(os.path.join(self.absent, "away00.jpg"))

        log = self._sweep()

        self.assertIn("Pruned 2 stale analysis entries.", log)
        after = self._analysis()
        self.assertNotIn("live00.jpg", after)
        self.assertNotIn("away00.jpg", after)
        self.assertEqual(len(after), len(rows) - 2)

    def test_legitimately_removed_camera_is_still_pruned(self):
        """A dir dropped from watch_dirs is not 'absent' — its rows must go."""
        rows = self._seed()
        settings = os.path.join(self.root, "settings.json")
        with open(settings, "w") as f:
            json.dump({"watch_dirs": [self.live], "max_age_days": 3650,
                       "max_dir_gb": 1000}, f)
        with redirect_stdout(io.StringIO()):
            analyze_images.main(retention_only=True, settings_path=settings)
        after = self._analysis()
        self.assertEqual(set(after), {k for k in rows if k.startswith("live")})

    def test_all_dirs_absent_prunes_nothing(self):
        rows = self._seed(n_live=5, n_absent=5)
        os.rename(self.live, self.live + ".moved")
        os.rename(self.absent, self.absent + ".moved")

        self._sweep()

        self.assertEqual(len(self._analysis()), len(rows))


if __name__ == "__main__":
    unittest.main()
