"""YOLO-gated individual e2b scans (no Ollama)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scans
import analyze_images as ai


class TestScansFor(unittest.TestCase):
    def test_front_person_is_postal_then_porch(self):
        got = [s["id"] for s in scans.scans_for("front", ["person"], limit=2)]
        self.assertEqual(got, ["postal", "porch"])

    def test_front_dog_only_is_animal(self):
        got = [s["id"] for s in scans.scans_for("front", ["dog"], limit=2)]
        self.assertEqual(got, ["animal"])

    def test_front_person_and_dog(self):
        got = [s["id"] for s in scans.scans_for("front", ["person", "dog"], limit=2)]
        self.assertEqual(got, ["postal", "porch"])  # dog_walk is 4th, over limit

    def test_front_person_and_dog_three(self):
        got = [s["id"] for s in scans.scans_for("front", ["person", "dog"], limit=4)]
        self.assertEqual(got, ["postal", "porch", "animal", "dog_walk"])

    def test_back_person_only_asks_nothing(self):
        # approaching_house dropped — do not spend 40s on it
        self.assertEqual(scans.scans_for("back", ["person"], limit=2), [])

    def test_back_dog_is_animal(self):
        got = [s["id"] for s in scans.scans_for("back", ["dog"], limit=2)]
        self.assertEqual(got, ["animal"])

    def test_back_person_and_dog(self):
        got = [s["id"] for s in scans.scans_for("back", ["person", "dog"], limit=2)]
        self.assertEqual(got, ["animal", "dog_walk"])

    def test_car_only_asks_nothing(self):
        self.assertEqual(scans.scans_for("front", ["car"], limit=2), [])

    def test_face_counts_as_person_for_postal(self):
        got = [s["id"] for s in scans.scans_for("front", ["face"], limit=1)]
        self.assertEqual(got, ["postal"])


class TestDropped(unittest.TestCase):
    def test_approaching_not_a_live_scan(self):
        ids = [s["id"] for s in scans.SCANS]
        self.assertNotIn("approaching", "".join(ids))
        self.assertIn("approaching_house", scans.DROPPED_FLAGS)
        self.assertIn("car_access", scans.DROPPED_FLAGS)
        self.assertIn("weapon_detected", scans.DROPPED_FLAGS)


class TestDeepPassNoScan(unittest.TestCase):
    def test_backyard_person_returns_zero_calls(self):
        ai.RATE_LIMITED = False
        result, n = ai.run_deep_pass(
            "/mnt/models/Webcam22/x.jpg", "x.jpg", True, None, "priority",
            fp_labels=["person"])
        self.assertIsNone(result)
        self.assertEqual(n, 0)
