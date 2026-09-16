"""Unit tests for the versioned analysis catalog contract.

Run from the repo root:  python3 -m unittest tests.test_catalog
No third-party deps. catalog.py must import without OpenCV.
"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog


class TestKind(unittest.TestCase):
    def test_bare_person_is_preliminary_not_verified(self):
        rec = {"person": True}
        self.assertEqual(catalog.kind(rec), "preliminary")
        self.assertFalse(catalog.is_llm_verified(rec))
        self.assertFalse(catalog.is_awaiting_backfill(rec))
        self.assertFalse(catalog.in_backfill_pool(rec))

    def test_car_no_trigger(self):
        rec = {"car": True, "_llm_skip": "no_trigger"}
        self.assertEqual(catalog.kind(rec), "no_trigger")
        self.assertTrue(catalog.is_awaiting_backfill(rec))
        self.assertTrue(catalog.in_backfill_pool(rec))
        self.assertFalse(catalog.is_llm_verified(rec))

    def test_fast_pass_negative(self):
        rec = {"fast_pass": "negative"}
        self.assertEqual(catalog.kind(rec), "negative")
        self.assertTrue(catalog.is_awaiting_backfill(rec))
        self.assertTrue(catalog.in_backfill_pool(rec))
        self.assertFalse(catalog.is_llm_verified(rec))

    def test_budget_skip_not_in_backfill(self):
        rec = {"person": True, "fast_pass": "partial", "_llm_skip": "budget"}
        self.assertEqual(catalog.kind(rec), "skip")
        self.assertFalse(catalog.is_awaiting_backfill(rec))
        self.assertFalse(catalog.in_backfill_pool(rec))
        self.assertFalse(catalog.is_llm_verified(rec))

    def test_person_with_llm_is_verified(self):
        rec = {"person": True, "_llm": {"porch_access": False}}
        self.assertEqual(catalog.kind(rec), "verified")
        self.assertTrue(catalog.is_llm_verified(rec))
        self.assertFalse(catalog.is_awaiting_backfill(rec))
        self.assertFalse(catalog.in_backfill_pool(rec))

    def test_empty_llm_is_not_verified(self):
        rec = {"_llm": {}}
        self.assertFalse(catalog.is_llm_verified(rec))
        self.assertEqual(catalog.kind(rec), "empty")


class TestTimelinePersistable(unittest.TestCase):
    def test_yolo_only_and_motion_are_not_persistable(self):
        self.assertFalse(catalog.is_timeline_persistable({"person": True}))
        self.assertFalse(catalog.is_timeline_persistable(
            {"fast_pass": "negative"}))
        self.assertFalse(catalog.is_timeline_persistable(
            {"person": True, "fast_pass": "partial", "_llm_skip": "budget"}))
        self.assertFalse(catalog.is_timeline_persistable(None))

    def test_llm_person_visit_is_persistable(self):
        rec = {"person": True, "_llm": {"porch_access": False}}
        self.assertTrue(catalog.is_timeline_persistable(rec))

    def test_ha_flag_without_yolo_is_persistable(self):
        rec = {"_llm": {"animal_detected": True, "animal_type": "cat"}}
        self.assertTrue(catalog.is_timeline_persistable(rec))

    def test_car_only_verified_is_not_persistable(self):
        rec = {"car": True, "_llm": {"car_access": False}}
        self.assertFalse(catalog.is_timeline_persistable(rec))

    def test_llm_car_access_is_persistable(self):
        rec = {"car": True, "_llm": {"car_access": True}}
        self.assertTrue(catalog.is_timeline_persistable(rec))


class TestStamp(unittest.TestCase):
    def test_stamp_adds_schema_and_yolo(self):
        rec = {"person": True, "car": True}
        out = catalog.stamp(rec)
        self.assertEqual(out["_schema"], 1)
        self.assertEqual(out["_schema"], catalog.SCHEMA_VERSION)
        self.assertEqual(out["_yolo"], ["car", "person"])
        self.assertTrue(out["person"])
        self.assertTrue(out["car"])
        self.assertNotIn("_schema", rec)
        self.assertNotIn("_yolo", rec)

    def test_stamp_does_not_invent_ha_flags(self):
        out = catalog.stamp({"person": True})
        for flag in (
            "porch_access", "postal_delivery", "dog_walked",
            "car_access", "animal_detected", "weapon_detected",
        ):
            self.assertNotIn(flag, out)
        self.assertEqual(set(out) - {"person"}, {"_schema", "_yolo"})

    def test_stamp_keeps_existing_yolo(self):
        rec = {"person": True, "_yolo": []}
        out = catalog.stamp(rec)
        self.assertEqual(out["_yolo"], [])
        self.assertEqual(out["_schema"], 1)

    def test_stamp_non_dict(self):
        self.assertIsNone(catalog.stamp(None))
        self.assertEqual(catalog.stamp("x"), "x")
        self.assertEqual(catalog.kind(None), "empty")
        self.assertFalse(catalog.is_llm_verified(None))


class TestDetectorAndPool(unittest.TestCase):
    def test_detector_true_labels_yolo_only(self):
        rec = {"person": True, "car": False, "porch_access": True, "dog": True}
        self.assertEqual(catalog.detector_true_labels(rec), ["dog", "person"])
        self.assertEqual(catalog.detector_true_labels(None), [])

    def test_car_only_partial_is_in_backfill_pool(self):
        rec = {"car": True, "fast_pass": "partial"}
        self.assertEqual(catalog.kind(rec), "preliminary")
        self.assertFalse(catalog.is_awaiting_backfill(rec))
        self.assertTrue(catalog.in_backfill_pool(rec))

    def test_other_skip_reasons(self):
        for reason in ("llm_failed", "low_mem", "ollama_down", "llm_disabled"):
            rec = {"person": True, "fast_pass": "partial", "_llm_skip": reason}
            self.assertEqual(catalog.kind(rec), "skip", reason)
            self.assertFalse(catalog.is_awaiting_backfill(rec), reason)
            self.assertFalse(catalog.in_backfill_pool(rec), reason)


class TestImportLight(unittest.TestCase):
    def test_import_does_not_load_opencv_or_analyze_images(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "import catalog\n"
            "assert catalog.SCHEMA_VERSION == 1\n"
            "assert 'cv2' not in sys.modules\n"
            "assert 'analyze_images' not in sys.modules\n"
        ) % root
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_analyze_images_imports_without_opencv(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "sys.modules['cv2'] = None\n"
            "class _Block:\n"
            "    def find_spec(self, name, path, target=None):\n"
            "        if name in ('cv2', 'numpy') or (name or '').startswith('cv2.') or (name or '').startswith('numpy.'):\n"
            "            raise ImportError('blocked')\n"
            "        return None\n"
            "sys.meta_path.insert(0, _Block())\n"
            "import analyze_images as ai\n"
            "assert ai.cv2 is None\n"
            "assert ai.camera_kind('/mnt/models/Webcam21/x.jpg') == 'front'\n"
        ) % root
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
