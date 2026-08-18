"""Pure-helper tests for HA schema wiring (no Ollama, no images)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analyze_images as ai


class TestCameraKind(unittest.TestCase):
    def test_front_dir_and_ip(self):
        self.assertEqual(ai.camera_kind("/mnt/models/Webcam21/foo.jpg"), "front")
        self.assertEqual(ai.camera_kind("10.0.0.21_01_x.jpg"), "front")

    def test_back_dir_and_ip(self):
        self.assertEqual(ai.camera_kind("/mnt/models/Webcam22/foo.jpg"), "back")
        self.assertEqual(ai.camera_kind("10.0.0.22_01_x.jpg"), "back")


class TestSchemaForKind(unittest.TestCase):
    def test_front(self):
        schema = ai.schema_for_kind("front")
        self.assertEqual(ai.max_tokens_for_kind("front"), 220)
        self.assertIn("postal_delivery", schema["properties"])
        self.assertNotIn("weapon_detected", schema["properties"])

    def test_back(self):
        schema = ai.schema_for_kind("back")
        self.assertEqual(ai.max_tokens_for_kind("back"), 160)
        self.assertIn("weapon_detected", schema["properties"])
        self.assertNotIn("postal_delivery", schema["properties"])


class TestTrigger(unittest.TestCase):
    def test_person_or_animal(self):
        self.assertTrue(ai.llm_should_trigger({"person": True}))
        self.assertTrue(ai.llm_should_trigger({"dog": True}))
        self.assertTrue(ai.llm_should_trigger({"cat": True}))
        self.assertTrue(ai.llm_should_trigger({"bird": True}))
        self.assertTrue(ai.llm_should_trigger({"person": True, "car": True}))

    def test_car_only_no_llm(self):
        self.assertFalse(ai.llm_should_trigger({"car": True}))
        self.assertFalse(ai.llm_should_trigger({}))
        self.assertFalse(ai.llm_should_trigger(None))


class TestMerge(unittest.TestCase):
    def test_keeps_yolo(self):
        fp = {"person": True, "car": True, "fast_pass": "partial"}
        llm = {"postal_delivery": False, "postal_how": "none",
               "porch_access": True, "animal_detected": True,
               "animal_type": "dog", "person": False, "_yolo": ["person", "car"]}
        rec = ai.merge_llm_into_fastpass(
            fp, llm, model="gemma4:e2b", duration_s=1.2, schema=ai.FRONT_SCHEMA)
        self.assertTrue(rec["person"])
        self.assertTrue(rec["car"])
        self.assertEqual(rec["postal_how"], "none")
        self.assertTrue(rec["porch_access"])
        self.assertEqual(rec["_yolo"], ["person", "car"])
        self.assertNotIn("fast_pass", rec)
        self.assertFalse(rec["dog_walked"])
        self.assertFalse(rec["opens_box"])
        self.assertEqual(rec["car_outfit"], "")
        self.assertEqual(rec["car_color"], "")

    def test_skip_low_mem(self):
        rec = ai.merge_llm_into_fastpass({"person": True, "dog": True}, None, skip_reason="low_mem")
        self.assertTrue(rec["person"])
        self.assertTrue(rec["dog"])
        self.assertEqual(rec["fast_pass"], "partial")
        self.assertEqual(rec["_llm_skip"], "low_mem")


if __name__ == "__main__":
    unittest.main()
