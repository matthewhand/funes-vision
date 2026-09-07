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

    def test_front_dog_only_is_yolo_seed(self):
        self.assertEqual(scans.scans_for("front", ["dog"], limit=2), [])
        self.assertEqual(scans.yolo_animal_seed(["dog"]),
                         {"animal_detected": True, "animal_type": "dog"})

    def test_front_person_and_dog(self):
        got = [s["id"] for s in scans.scans_for("front", ["person", "dog"], limit=2)]
        self.assertEqual(got, ["postal", "dog_walk"])

    def test_front_person_and_dog_three(self):
        got = [s["id"] for s in scans.scans_for("front", ["person", "dog"], limit=4)]
        self.assertEqual(got, ["postal", "dog_walk", "porch", "package"])

    def test_back_person_only_asks_nothing(self):
        # approaching_house dropped — do not spend 40s on it
        self.assertEqual(scans.scans_for("back", ["person"], limit=2), [])

    def test_back_dog_is_yolo_seed_not_a_scan(self):
        self.assertEqual(scans.scans_for("back", ["dog"], limit=2), [])
        self.assertEqual(scans.yolo_animal_seed(["dog"]),
                         {"animal_detected": True, "animal_type": "dog"})

    def test_back_person_and_dog(self):
        got = [s["id"] for s in scans.scans_for("back", ["person", "dog"], limit=2)]
        self.assertEqual(got, ["dog_walk"])

    def test_car_only_asks_nothing(self):
        self.assertEqual(scans.scans_for("front", ["car"], limit=2), [])

    def test_face_counts_as_person_for_postal(self):
        got = [s["id"] for s in scans.scans_for("front", ["face"], limit=1)]
        self.assertEqual(got, ["postal"])

    def test_porch_gate_frees_package_slot(self):
        got = [s["id"] for s in scans.scans_for(
            "front", ["person"], limit=2, skip={"porch"})]
        self.assertEqual(got, ["postal", "package"])

    def test_union_schema_keeps_porch_from_seed(self):
        asked = scans.scans_for("front", ["person"], limit=2, skip={"porch"})
        schema = scans.union_schema(asked, {"porch_access": False})
        self.assertIn("porch_access", schema["properties"])
        self.assertIn("postal_delivery", schema["properties"])
        self.assertNotIn("animal_detected", schema["properties"])


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

    def test_backyard_dog_is_yolo_seed_no_llm(self):
        ai.RATE_LIMITED = False
        result, n = ai.run_deep_pass(
            "/mnt/models/Webcam22/x.jpg", "x.jpg", True, None, "priority",
            fp_labels=["dog"])
        self.assertEqual(n, 0)
        self.assertEqual(result["animal_detected"], True)
        self.assertEqual(result["animal_type"], "dog")
        self.assertEqual(result["_scans"], ["yolo_animal"])

    def _run_gated_front(self, centre):
        import tempfile
        orig = ai.IGNORE_REGIONS
        orig_st, orig_log = ai.INFERENCE_STATUS, ai.INFERENCE_LOG
        ai.RATE_LIMITED = False
        ai.IGNORE_REGIONS = [{
            "id": "porch", "camera": "front", "mode": "gate", "scan": "porch",
            "labels": ["person", "face", "body"], "enabled": True,
            "polygon": [[0.66, 0.28], [1.0, 0.22], [1.0, 1.0], [0.62, 1.0]],
        }]
        fp = {"person": True, "_centres": {"person": centre}}
        with tempfile.TemporaryDirectory() as d:
            ai.INFERENCE_STATUS = os.path.join(d, "st.json")
            ai.INFERENCE_LOG = os.path.join(d, "log.json")
            try:
                return ai.run_deep_pass(
                    "front.jpg", "front.jpg", False, None, "priority",
                    fp_labels=fp)
            finally:
                ai.IGNORE_REGIONS = orig
                ai.INFERENCE_STATUS = orig_st
                ai.INFERENCE_LOG = orig_log

    def test_front_path_person_gets_porch_false_from_gate(self):
        result, n = self._run_gated_front((0.52, 0.28))
        self.assertEqual(result["porch_access"], False)
        self.assertNotIn("porch", result.get("_scans") or [])

    def test_front_tile_person_gets_porch_true_from_gate(self):
        result, n = self._run_gated_front((0.88, 0.45))
        self.assertEqual(result["porch_access"], True)
        self.assertNotIn("porch", result.get("_scans") or [])
