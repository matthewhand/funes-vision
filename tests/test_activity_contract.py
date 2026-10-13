"""Synthetic-only, temporary-directory contract regressions; no cameras or models."""
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from funes.server import SnapshotCatalog, MAX_FRAMES

JPEG_A = b'\xff\xd8\xffA\xff\xd9'
JPEG_B = b'\xff\xd8\xffB\xff\xd9'

class ActivityContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="funes-public-cycle2-synthetic-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for camera in ("DemoFront", "DemoBack"):
            (self.root / camera).mkdir()
        self.catalog = SnapshotCatalog(self.root)

    def put(self, camera, name, raw=JPEG_A, timestamp=None):
        f = self.root / camera / name
        f.write_bytes(raw)
        if timestamp is not None:
            os.utime(f, (timestamp, timestamp))
        return f

    def test_read_only_contract_preserves_unclassified_provenance(self):
        self.put("DemoFront", "unknown.jpg", timestamp=1700000000)
        result = self.catalog.snapshot()
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["data_source"], "watched-folder-preview")
        self.assertTrue(result["read_only"])
        self.assertEqual((result["count"], result["visit_count"]), (1, 1))
        f = result["frames"][0]
        self.assertEqual(f["objects"], [])
        self.assertFalse(f["synthetic_fixture"])
        self.assertEqual(f["capture_time_source"], "filesystem-mtime")
        self.assertEqual(f["detector_status"], "not run")
        self.assertEqual(result["inference"]["vision_decision_model"], "not run")
        self.assertEqual(result["scan"]["max_frames"], MAX_FRAMES)

    def test_interleaved_camera_snapshots_keep_separate_visits(self):
        for camera, seconds in (("DemoFront", [1000, 1030, 1300]),
                                ("DemoBack", [1010, 1060])):
            for i, sec in enumerate(seconds):
                self.put(camera, f"{i:03}.jpg", timestamp=sec)
        r = self.catalog.snapshot()
        self.assertEqual(r["count"], 5)
        self.assertEqual(r["visit_count"], 3)
        self.assertEqual(sorted(v["frame_count"] for v in r["visits"]), [1, 2, 2])
        self.assertTrue(all(v["camera"] in ("DemoFront", "DemoBack") for v in r["visits"]))

    def test_round_robin_cap_and_bounded_fingerprint_cache(self):
        for camera in ("DemoFront", "DemoBack"):
            folder = self.root / camera
            for i in range(600):
                (folder / f"{i:04}.jpg").write_bytes(JPEG_A)
        r = self.catalog.snapshot()
        self.assertEqual(r["count"], MAX_FRAMES)
        self.assertEqual(sum(f["camera"] == "DemoFront" for f in r["frames"]), 250)
        self.assertEqual(sum(f["camera"] == "DemoBack" for f in r["frames"]), 250)
        self.assertTrue(r["scan"]["possibly_truncated"])
        self.assertLessEqual(len(self.catalog._fingerprints), MAX_FRAMES)
        self.assertEqual(self.catalog.snapshot()["count"], MAX_FRAMES)
        self.assertLessEqual(len(self.catalog._fingerprints), MAX_FRAMES)
        for file in (self.root/"DemoFront").glob("*.jpg"):
            file.unlink()
        latest = self.catalog.snapshot()
        self.assertEqual(latest["count"], MAX_FRAMES)
        self.assertTrue(all(f["camera"] == "DemoBack" for f in latest["frames"]))
        self.assertLessEqual(len(self.catalog._fingerprints), MAX_FRAMES)

    def test_rewrite_same_name_size_and_mtime_invalidates_prior_fixture(self):
        file = self.put("DemoFront", "demo-front-01.jpg", JPEG_A, 1700000000)
        digest = hashlib.sha256(JPEG_A).hexdigest()
        self.catalog._manifest[(file.name, digest)] = {
            "timestamp": "2026-10-01T09:00:00+00:00",
            "objects": ["person"], "description": "Fictional person"
        }
        first = self.catalog.snapshot()["frames"][0]
        self.assertTrue(first["synthetic_fixture"])
        original = file.stat().st_mtime_ns
        time.sleep(0.005)
        file.write_bytes(JPEG_B)
        os.utime(file, ns=(original, original))
        next_frame = self.catalog.snapshot()["frames"][0]
        self.assertFalse(next_frame["synthetic_fixture"])
        self.assertEqual(next_frame["objects"], [])
        self.assertEqual(next_frame["capture_time_source"], "filesystem-mtime")

    def test_partial_jpeg_and_symlink_not_indexed(self):
        self.put("DemoFront", "partial.jpg", b'\xff\xd8\xffincomplete')
        valid = self.put("DemoBack", "valid.jpg")
        (self.root/"DemoFront"/"linked.jpg").symlink_to(valid)
        result = self.catalog.snapshot()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["frames"][0]["filename"], "valid.jpg")

if __name__ == "__main__":
    unittest.main()
