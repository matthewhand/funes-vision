"""#22: a non-positive max_dir_gb must disable disk-budget eviction, not wipe
the camera dir.

Every retention category (empty, YOLO-only, persistable, unanalyzed) is
evictable, so `total > budget` held for *any* non-empty dir when the budget was
0 or negative and a single sweep removed the whole archive. run_health_checks
already guarded the same key with `if budget`; the two call sites disagreed.

Frames here are inside max_age_days, so the age pass cannot account for any
deletion: what disappears came from the budget pass alone.

Run from the repo root:  python3 -m unittest discover -s tests
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analyze_images


class TestRetentionDiskBudgetGuard(unittest.TestCase):
    GLOBALS = ("MAX_AGE_DAYS", "MAX_DIR_GB", "PERSIST_BUDGET_PCT",
               "RETENTION_LOG", "GATE_IGNORE_LABELS")

    def setUp(self):
        self._saved = {n: getattr(analyze_images, n) for n in self.GLOBALS}
        self.td = tempfile.TemporaryDirectory()
        self.dir = self.td.name
        analyze_images.RETENTION_LOG = os.path.join(self.dir, "retention_log.json")
        analyze_images.GATE_IGNORE_LABELS = ["car"]
        analyze_images.MAX_AGE_DAYS = 30      # age pass cannot fire on young frames
        analyze_images.PERSIST_BUDGET_PCT = 20.0

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(analyze_images, name, value)
        self.td.cleanup()

    def _frame(self, name, age_days, size=100):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(b"x" * size)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def _four_ages_one_of_each_kind(self):
        """One frame per evictable category, so a budget pass reaches all 4."""
        ages = (0.2, 0.1, 0.05, 0.01)
        rows = {
            "empty.jpg": {"fast_pass": "negative"},
            "yolo.jpg": {"person": True, "fast_pass": "partial"},
            "persistable.jpg": {"person": True, "_llm": {"porch_access": True}},
            "unknown.jpg": None,   # uncatalogued backlog
        }
        for name, age in zip(rows, ages, strict=True):
            self._frame(name, age)
        return rows

    def _run(self, max_dir_gb):
        analyze_images.MAX_DIR_GB = max_dir_gb
        rows = {k: v for k, v in self._four_ages_one_of_each_kind().items()}
        deleted = analyze_images.apply_retention(self.dir, rows, set())
        left = sorted(f for f in os.listdir(self.dir) if f.lower().endswith(".jpg"))
        return deleted, left

    def test_zero_budget_removes_nothing(self):
        deleted, left = self._run(0)
        self.assertEqual(deleted, set())
        self.assertEqual(left, ["empty.jpg", "persistable.jpg", "unknown.jpg", "yolo.jpg"])

    def test_negative_budget_removes_nothing(self):
        deleted, left = self._run(-3)
        self.assertEqual(deleted, set())
        self.assertEqual(len(left), 4)

    def test_unusable_budget_fails_closed(self):
        # None/string junk must not raise out of the budget computation either.
        for bogus in (None, "not-a-number"):
            with self.subTest(budget=bogus):
                analyze_images.MAX_DIR_GB = self._saved["MAX_DIR_GB"]
                rows = {k: v for k, v in self._four_ages_one_of_each_kind().items()}
                analyze_images.MAX_DIR_GB = bogus
                deleted = analyze_images.apply_retention(self.dir, rows, set())
                self.assertEqual(deleted, set())

    def test_real_budget_still_evicts(self):
        # A genuinely tiny budget must keep working: eviction is the feature.
        deleted, left = self._run(1e-9)
        self.assertEqual(len(deleted), 4)
        self.assertEqual(left, [])

    def test_real_budget_evicts_in_category_order(self):
        analyze_images.MAX_DIR_GB = 1e-9
        rows = self._four_ages_one_of_each_kind()
        deleted = analyze_images.apply_retention(self.dir, rows, set())
        # Oldest first, negatives before persistable before uncatalogued.
        self.assertEqual(deleted, set(rows))

    def test_pins_survive_and_age_pass_still_fires(self):
        # The guard only disables the *disk-budget* passes.
        analyze_images.MAX_DIR_GB = 0
        self._frame("ancient.jpg", 100)
        deleted = analyze_images.apply_retention(
            self.dir, {"ancient.jpg": {"fast_pass": "negative"}}, {"ancient.jpg"})
        self.assertEqual(deleted, set())          # pin wins over the age pass

        analyze_images.PERSIST_BUDGET_PCT = 20.0
        with tempfile.TemporaryDirectory() as other:
            analyze_images.RETENTION_LOG = os.path.join(other, "retention_log.json")
            path = os.path.join(other, "ancient2.jpg")
            with open(path, "wb") as f:
                f.write(b"x" * 100)
            stamp = time.time() - 100 * 86400
            os.utime(path, (stamp, stamp))
            deleted = analyze_images.apply_retention(
                other, {"ancient2.jpg": {"fast_pass": "negative"}}, set())
        self.assertEqual(deleted, {"ancient2.jpg"})

    def test_apply_settings_clamps_budget_to_zero(self):
        analyze_images.apply_settings({"max_dir_gb": -9})
        self.assertEqual(analyze_images.MAX_DIR_GB, 0)
        analyze_images.apply_settings({"max_dir_gb": 0})
        self.assertEqual(analyze_images.MAX_DIR_GB, 0)
        analyze_images.apply_settings({"max_dir_gb": 7.5})
        self.assertEqual(analyze_images.MAX_DIR_GB, 7.5)


if __name__ == "__main__":
    unittest.main()
