"""#29: a partially recovered catalog must not make verified frames deletable.

_recover_truncated_json salvages a valid prefix, and load_json_file used to
rewrite analysis.json in place with it. Rows past the truncation point were
gone for good, so the frames they referenced became "uncatalogued" — no longer
persistable — and the age/budget passes deleted real LLM-verified evidence.

Two guards now hold: the recovered prefix is parked in a ``.recovered``
sidecar so the original bytes (and the truncated tail) survive, and while a
catalog is a partial recovery retention treats uncatalogued frames as
non-deletable rather than un-persistable.

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

VERIFIED = {"person": True, "_llm": {"porch_access": True}}


class TestPartialCatalogRecovery(unittest.TestCase):
    GLOBALS = ("BASE_DIR", "WATCH_DIRS", "_settings_path", "MAX_AGE_DAYS",
               "MAX_DIR_GB", "PERSIST_BUDGET_PCT", "RETENTION_LOG",
               "GATE_IGNORE_LABELS", "PIPELINE_LOCK")

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = self.td.name
        self.img = os.path.join(self.root, "cam1")
        os.makedirs(self.img)
        self.analysis_file = os.path.join(self.root, "analysis.json")
        self._saved = {n: getattr(analyze_images, n, None) for n in self.GLOBALS}
        analyze_images.BASE_DIR = self.root
        analyze_images._settings_path = os.path.join(self.root, "settings.json")
        analyze_images.WATCH_DIRS = [self.img]
        analyze_images.MAX_AGE_DAYS = 1
        analyze_images.MAX_DIR_GB = 100
        analyze_images.PERSIST_BUDGET_PCT = 20.0
        analyze_images.RETENTION_LOG = os.path.join(self.root, "retention_log.json")
        analyze_images.GATE_IGNORE_LABELS = ["car"]
        analyze_images.PIPELINE_LOCK = os.path.join(self.root, "pipeline.lock")
        self.addCleanup(self._clear_partial_marks)
        patcher = mock.patch.object(analyze_images, "ollama_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(analyze_images, name, value)
        self.td.cleanup()

    @staticmethod
    def _clear_partial_marks():
        """Never leak the partial-recovery mark into another test's retention."""
        marks = getattr(analyze_images, "_PARTIAL_CATALOGS", None)
        if marks is not None:
            marks.clear()

    def _seed(self, n=30, n_verified=10):
        rows = {}
        for i in range(n):
            name = f"f{i:02d}.jpg"
            with open(os.path.join(self.img, name), "wb") as f:
                f.write(b"x" * 100)
            stamp = time.time() - 200 * 86400     # far past max_age_days
            os.utime(os.path.join(self.img, name), (stamp, stamp))
            rows[name] = dict(VERIFIED) if i < n_verified else {"fast_pass": "negative"}
        with open(self.analysis_file, "w") as f:
            json.dump(rows, f, indent=2)
        return rows

    def _sweep(self):
        settings = os.path.join(self.root, "settings.json")
        with open(settings, "w") as f:
            json.dump({"watch_dirs": [self.img], "max_age_days": 1,
                       "max_dir_gb": 100}, f)
        out = io.StringIO()
        with redirect_stdout(out):
            analyze_images.main(retention_only=True, settings_path=settings)
        return out.getvalue()

    def _kept_verified(self, n_verified=10):
        return sum(1 for i in range(n_verified)
                   if os.path.exists(os.path.join(self.img, f"f{i:02d}.jpg")))

    def _truncate(self, frac=0.25):
        with open(self.analysis_file) as f:
            raw = f.read()
        corrupt = raw[: int(len(raw) * frac)]
        with open(self.analysis_file, "w") as f:
            f.write(corrupt)
        return corrupt

    def test_intact_catalog_keeps_verified_frames(self):
        self._seed()
        self._sweep()
        self.assertEqual(self._kept_verified(), 10)

    def test_truncated_catalog_does_not_delete_lost_rows_frames(self):
        """The regression: recovery used to drop the tail, then retention
        deleted the frames whose rows were lost."""
        self._seed()
        self._truncate()

        log = self._sweep()

        self.assertIn("incomplete (partially recovered)", log)
        self.assertIn("protected", log)
        self.assertEqual(self._kept_verified(), 10)

    def test_recovery_does_not_rewrite_the_original(self):
        self._seed()
        corrupt = self._truncate()

        self._sweep()

        with open(self.analysis_file) as f:
            self.assertEqual(f.read(), corrupt)          # tail still on disk
        sidecar = self.analysis_file + ".recovered"
        self.assertTrue(os.path.exists(sidecar))
        with open(sidecar) as f:
            recovered = json.load(f)
        self.assertTrue(recovered)
        self.assertLess(len(recovered), 10)                # a prefix, not the whole thing

    def test_partial_mark_holds_until_the_backlog_is_rebuilt(self):
        """#56 Gap B: the mark used to be cleared the moment the file parsed.

        Sweep 1's own flush completes the JSON, so a clean parse on sweep 2
        said "recovered" when all that had happened is that 2 of 10 missing
        rows came back. Every remaining uncatalogued frame then read as
        ordinary backlog and was deleted. The mark must now survive until the
        frames the catalog cannot account for actually have rows again, and
        release by itself once they do.

        (This test previously asserted the opposite -- that a clean reload of
        a one-row catalog cleared the mark -- because that was the behaviour
        the fix removes.)
        """
        self._seed()
        self._truncate()
        self._sweep()
        # A clean-but-still-incomplete catalog: 29 of the 30 frames have no row.
        with open(self.analysis_file, "w") as f:
            json.dump({"f00.jpg": dict(VERIFIED)}, f)
        log = self._sweep()
        self.assertIn("incomplete", log)
        self.assertIn("are protected", log)
        self.assertIn("f29.jpg", sorted(os.listdir(self.img)))

        # ... and the protection releases once the backlog is genuinely rebuilt.
        rows = {f"f{i:02d}.jpg": dict(VERIFIED) for i in range(30)}
        with open(self.analysis_file, "w") as f:
            json.dump(rows, f)
        log = self._sweep()
        self.assertNotIn("are protected", log)
        self.assertNotIn("incomplete", log)
        self.assertEqual(self._kept_verified(), 10)

    def test_mark_is_derived_per_sweep_not_carried_over(self):
        """The marks record what *this* sweep's catalog load found, so a verdict
        about one tree can never reach the next sweep (nor another test)."""
        self._seed()
        self._truncate()
        self._sweep()
        self.assertFalse(analyze_images.catalog_is_partial(self.analysis_file))
        self.assertEqual(analyze_images._PARTIAL_CATALOGS, {})

    def test_mark_holds_on_load_and_releases_on_a_complete_one(self):
        """The mark's own state machine, without a sweep in the way.

        Marks are per-sweep (main() clears them on the way out), so this drives
        load_json_file directly -- the same seam TestJsonRecovery uses.
        """
        self._seed()
        with open(self.analysis_file, "w") as f:
            json.dump({"f00.jpg": dict(VERIFIED)}, f)
        analyze_images.load_json_file(self.analysis_file, {}, catalog=True)
        self.assertTrue(analyze_images.catalog_is_partial(self.analysis_file))
        rows = {f"f{i:02d}.jpg": dict(VERIFIED) for i in range(30)}
        with open(self.analysis_file, "w") as f:
            json.dump(rows, f)
        analyze_images.load_json_file(self.analysis_file, {}, catalog=True)
        self.assertFalse(analyze_images.catalog_is_partial(self.analysis_file))

    def test_transient_backlog_does_not_arm_the_mark(self):
        """#56: a frame that arrived *after* the last catalog write is ordinary
        backlog, not a lost row. If it armed the mark, every sweep of a working
        installation (a frame always lands between sweeps) would hold the
        protection for ever and unanalysed frames would never age out."""
        self._seed()
        self._sweep()                                  # writes the catalog
        with open(os.path.join(self.img, "brand_new.jpg"), "wb") as f:
            f.write(b"x" * 100)
        os.utime(os.path.join(self.img, "brand_new.jpg"),
                 (time.time(), time.time()))         # newer than the catalog
        self._sweep()
        self.assertFalse(analyze_images.catalog_is_partial(self.analysis_file))

    def test_non_catalog_files_do_not_arm_the_protection(self):
        # A corrupt retention_log.json must not make every frame undeletable.
        path = os.path.join(self.root, "retention_log.json")
        with open(path, "w") as f:
            f.write('[{"ts": 1, "cou')
        analyze_images.load_json_file(path, [])
        self.assertFalse(analyze_images.catalog_is_partial())
        self.assertFalse(analyze_images.catalog_is_partial(path))

    def test_non_catalog_corruption_is_reported_honestly(self):
        """#56: the non-catalog branch had no coverage at all and two defects.

        It claimed "original left intact, prefix saved to <sidecar>" for files
        (retention_log / alert_state / pins) that their own writer replaces
        wholesale on the very next append, and it really did drop a
        ``<path>.recovered`` sidecar next to them — litter that only ever
        confused the next reader.
        """
        for name, default in (("retention_log.json", []), ("alert_state.json", {}),
                              ("pins.json", [])):
            with self.subTest(name=name):
                path = os.path.join(self.root, name)
                with open(path, "w") as f:
                    f.write('[{"ts": 1, "cou' if default is list
                             else '{"seen": {"cam1": [1, 2')
                out = io.StringIO()
                with redirect_stdout(out):
                    data = analyze_images.load_json_file(path, default)
                log = out.getvalue()
                self.assertEqual(data, default)          # nothing recovered
                self.assertNotIn("left intact", log)
                self.assertIn("rebuilt from scratch on the next write", log)
                self.assertFalse(os.path.exists(path + ".recovered"))

    def test_non_catalog_recovery_does_not_park_a_sidecar(self):
        path = os.path.join(self.root, "alert_state.json")
        with open(path, "w") as f:
            f.write('{\n  "cam1": {"seen": {"x": 1}},\n  "cam2": {"see')
        out = io.StringIO()
        with redirect_stdout(out):
            data = analyze_images.load_json_file(path, {})
        self.assertIn("cam1", data)
        self.assertNotIn("cam2", data)
        self.assertFalse(analyze_images.catalog_is_partial(path))
        self.assertFalse(os.path.exists(path + ".recovered"))

    def test_categorued_rows_still_evict_while_partial(self):
        """Fail-closed applies to the *unknown*, not to the known: rows that
        survived the cut are still ordinary retention candidates."""
        self._seed()
        self._truncate()
        self._sweep()
        # f00.. are the surviving prefix (verified) -> kept by the persist cap;
        # the 20 negatives had no rows past the cut and are protected as unknown.
        left = sorted(f for f in os.listdir(self.img) if f.endswith(".jpg"))
        self.assertGreaterEqual(len(left), 2)
        self.assertIn("f00.jpg", left)

    def test_protection_clears_once_the_catalog_is_repaired(self):
        """#56: rewritten. This used to write a one-row catalog over a truncated
        one and assert the next sweep deleted f01.jpg -- which is only possible
        while 29 frames are still uncatalogued, i.e. it asserted the Gap B data
        loss. The release condition is now an empty backlog, so a *complete*
        catalog is what makes eviction resume: every frame has a row, and the
        aged negative among them is an ordinary retention candidate again."""
        self._seed()
        self._truncate()
        self._sweep()
        rows = {f"f{i:02d}.jpg": dict(VERIFIED) for i in range(10)}
        rows.update({f"f{i:02d}.jpg": {"fast_pass": "negative"} for i in range(10, 30)})
        with open(self.analysis_file, "w") as f:
            json.dump(rows, f)
        self._sweep()          # clean + complete catalog: normal retention resumes
        self.assertFalse(os.path.exists(os.path.join(self.img, "f11.jpg")))

    def test_missing_catalog_does_not_delete_verified_frames(self):
        """#56 Gap A: no analysis.json at all is not an empty archive.

        `{}` (or a missing file) made every frame unanalysed backlog, and pass 1
        runs *before* the analysis queue is built, so a single retention-only
        sweep deleted LLM-verified frames 40 days old. This is the state #40's
        risk section tells operators to create and the residue #23 leaves
        behind, so it has to be covered end to end.
        """
        self._seed()
        os.remove(self.analysis_file)

        log = self._sweep()

        self.assertIn("incomplete (missing)", log)
        self.assertIn("protected", log)
        self.assertEqual(self._kept_verified(), 10)

    def test_empty_catalog_does_not_delete_verified_frames(self):
        """#56 Gap A: the #23 residue -- a catalog that parses but has no rows
        is the same data loss as a missing one."""
        self._seed()
        with open(self.analysis_file, "w") as f:
            json.dump({}, f)

        log = self._sweep()

        self.assertIn("incomplete", log)
        self.assertEqual(self._kept_verified(), 10)

    def test_blank_catalog_does_not_delete_verified_frames(self):
        self._seed()
        open(self.analysis_file, "w").close()      # zero bytes

        log = self._sweep()

        self.assertIn("incomplete (blank)", log)
        self.assertEqual(self._kept_verified(), 10)

    def test_clean_but_incomplete_catalog_is_committed(self):
        """#56: only a catalog holding an *unparsable* tail must be left on disk
        (#29). A catalog that parsed but is missing rows has no tail to save,
        and refusing to commit it would stall the repair for ever."""
        self._seed()
        with open(os.path.join(self.img, "gone.jpg"), "wb") as f:
            f.write(b"x")
        rows = {f"f{i:02d}.jpg": dict(VERIFIED) for i in range(30)}
        rows["gone.jpg"] = dict(VERIFIED)
        with open(self.analysis_file, "w") as f:
            json.dump(rows, f)
        os.unlink(os.path.join(self.img, "gone.jpg"))

        log = self._sweep()

        self.assertIn("Pruned 1 stale analysis entries", log)
        with open(self.analysis_file) as f:
            self.assertNotIn("gone.jpg", json.load(f))

    def test_unparsable_catalog_is_still_not_committed(self):
        self._seed()
        self._truncate()
        self._sweep()
        with open(os.path.join(self.img, "gone.jpg"), "wb") as f:
            f.write(b"x")
        with open(self.analysis_file, "a") as f:
            f.write('  "gone.jpg": {"fast_pass": "negative"},\n  "trunc')
        self._sweep()
        with open(self.analysis_file) as f:
            self.assertIn("trunc", f.read())      # tail still on disk


if __name__ == "__main__":
    unittest.main()
