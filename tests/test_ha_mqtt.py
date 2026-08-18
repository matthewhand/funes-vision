"""Pure tests for HA MQTT vision payload (no broker)."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Prefer a local analyze_images if present (example checkout).
import ha_mqtt


class TestShouldPublish(unittest.TestCase):
    def test_car_only_no_trigger(self):
        self.assertFalse(ha_mqtt.should_publish({"car": True, "_llm_skip": "no_trigger"}))

    def test_negative(self):
        self.assertFalse(ha_mqtt.should_publish({"fast_pass": "negative"}))

    def test_analysed(self):
        self.assertTrue(ha_mqtt.should_publish({
            "person": True,
            "postal_delivery": False,
            "_llm": {"postal_delivery": False},
        }))

    def test_skip_budget_does_not_clobber(self):
        self.assertFalse(ha_mqtt.should_publish({
            "person": True, "fast_pass": "partial", "_llm_skip": "budget",
        }))

    def test_llm_failed_does_not_publish(self):
        self.assertFalse(ha_mqtt.should_publish({
            "person": True, "fast_pass": "partial", "_llm_skip": "llm_failed",
            "_llm_ms": 45,
        }))

    def test_ha_flag_without_llm_does_not_publish(self):
        self.assertFalse(ha_mqtt.should_publish({"porch_access": True}))

    def test_ha_flag_with_llm_publishes(self):
        self.assertTrue(ha_mqtt.should_publish({
            "porch_access": True, "_llm": {"porch_access": True},
        }))


class TestPayload(unittest.TestCase):
    def test_front_analysed(self):
        rec = {
            "person": True,
            "postal_delivery": False,
            "postal_how": "none",
            "dog_walked": False,
            "car_access": False,
            "enters_car": False,
            "exits_car": False,
            "car_outfit": "none",
            "car_color": "orange",
            "car_make": "none",
            "opens_box": False,
            "porch_access": False,
            "animal_detected": True,
            "animal_type": "dog",
            "_llm": {"postal_delivery": False, "animal_detected": True},
            "_llm_model": "gemma4:e2b",
            "_llm_ms": 46100,
        }
        p = ha_mqtt.ha_vision_payload("10.0.0.21_01_20260814100051444_MOTDEC.jpg", rec, e2b_loaded=True)
        self.assertEqual(p["camera"], "front_door")
        self.assertTrue(p["_llm"])
        self.assertEqual(p["skip_reason"], "")
        self.assertEqual(p["model"], "gemma4:e2b")
        self.assertEqual(p["_llm_ms"], 46100)
        self.assertTrue(p["e2b_loaded"])
        self.assertTrue(p["animal_detected"])
        self.assertNotIn("person", p)
        self.assertNotIn("approaching_house", p)
        self.assertTrue(p["ts"].startswith("2026-08-14T10:00:51.444"))

    def test_back_analysed(self):
        rec = {
            "dog": True,
            "dog_walked": False,
            "approaching_house": True,
            "leaving_house": False,
            "weapon_detected": False,
            "clothes_drying": False,
            "animal_detected": True,
            "animal_type": "dog",
            "_llm": {"approaching_house": True},
            "_llm_model": "gemma4:e2b",
            "_llm_ms": 81950,
        }
        p = ha_mqtt.ha_vision_payload("10.0.0.22_01_20260815064546223_MOTDEC.jpg", rec, e2b_loaded=False)
        self.assertEqual(p["camera"], "dog_cam")
        self.assertTrue(p["approaching_house"])
        self.assertNotIn("postal_delivery", p)
        self.assertNotIn("dog", p)
        self.assertEqual(p["skip_reason"], "")
        self.assertFalse(p["e2b_loaded"])

    def test_front_emits_all_flag_keys(self):
        rec = {
            "animal_detected": True,
            "animal_type": "dog",
            "_llm": {"animal_detected": True, "animal_type": "dog"},
            "_llm_model": "gemma4:e2b",
            "_llm_ms": 12,
        }
        p = ha_mqtt.ha_vision_payload("10.0.0.21_x.jpg", rec, e2b_loaded=True)
        import analyze_images as ai
        for k in ai.FRONT_FLAG_KEYS:
            self.assertIn(k, p)
        self.assertEqual(p["skip_reason"], "")
        self.assertTrue(p["_llm"])
        self.assertTrue(p["animal_detected"])
        self.assertFalse(p["postal_delivery"])

    def test_skip_reason_on_fail(self):
        rec = {"person": True, "fast_pass": "partial", "_llm_skip": "low_mem"}
        p = ha_mqtt.ha_vision_payload("10.0.0.21_01_20260814100051444_MOTDEC.jpg", rec)
        self.assertFalse(p["_llm"])
        self.assertEqual(p["skip_reason"], "low_mem")
        self.assertIsNone(p["_llm_model"])


class TestLatest(unittest.TestCase):
    def test_prefers_analysed_over_newer_skip(self):
        data = {
            "10.0.0.21_01_20260814100051444_MOTDEC.jpg": {
                "postal_delivery": False, "_llm": {"postal_delivery": False},
            },
            "10.0.0.21_01_20260814999999999_MOTDEC.jpg": {
                "person": True, "_llm_skip": "llm_disabled",
            },
            "10.0.0.22_01_20260815064546223_MOTDEC.jpg": {
                "approaching_house": True, "_llm": {"approaching_house": True},
            },
        }
        latest = ha_mqtt.latest_records(data)
        self.assertEqual(latest["front"][0], "10.0.0.21_01_20260814100051444_MOTDEC.jpg")
        self.assertEqual(latest["back"][0], "10.0.0.22_01_20260815064546223_MOTDEC.jpg")

    def test_ignores_car_only(self):
        data = {
            "10.0.0.21_01_20260814100051444_MOTDEC.jpg": {
                "car": True, "_llm_skip": "no_trigger",
            },
        }
        latest = ha_mqtt.latest_records(data)
        self.assertIsNone(latest["front"])

    def test_ignores_newer_llm_failed_even_if_only_recent(self):
        data = {
            "10.0.0.21_01_20260623100000000_MOTDEC.jpg": {
                "person": True, "fast_pass": "partial",
                "_llm_skip": "llm_failed", "_llm_ms": 45,
            },
            "10.0.0.22_01_20260813201652150_MOTDEC.jpg": {
                "dog": True, "fast_pass": "partial",
                "_llm_skip": "llm_failed", "_llm_ms": 36,
            },
            "10.0.0.21_01_20260814100051444_MOTDEC.jpg": {
                "postal_delivery": False, "animal_detected": True,
                "animal_type": "dog",
                "_llm": {"postal_delivery": False, "animal_detected": True},
            },
        }
        latest = ha_mqtt.latest_records(data)
        self.assertEqual(latest["front"][0], "10.0.0.21_01_20260814100051444_MOTDEC.jpg")
        self.assertIsNone(latest["back"])


class TestLoadMqttCfg(unittest.TestCase):
    def _load(self, path, extra_env=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("MQTT_")}
        if extra_env:
            env.update(extra_env)
        with mock.patch.object(ha_mqtt, "CONFIG_FILE", path):
            with mock.patch.dict(os.environ, env, clear=True):
                return ha_mqtt.load_mqtt_cfg()

    def test_missing_file_defaults_disabled(self):
        cfg = self._load("/no/such/integrations.json")
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["hosts"], [])
        self.assertFalse(hasattr(ha_mqtt, "DEFAULT_HOSTS"))

    def test_missing_mqtt_block_defaults_disabled(self):
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w") as f:
                json.dump({"slack": {"enabled": True}}, f)
            cfg = self._load(path)
            self.assertFalse(cfg["enabled"])
            self.assertEqual(cfg["hosts"], [])
        finally:
            os.unlink(path)

    def test_empty_mqtt_block_defaults_disabled(self):
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w") as f:
                json.dump({"mqtt": {}}, f)
            cfg = self._load(path)
            self.assertFalse(cfg["enabled"])
            self.assertEqual(cfg["hosts"], [])
        finally:
            os.unlink(path)

    def test_explicit_enabled_true(self):
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w") as f:
                json.dump({"mqtt": {"enabled": True, "host": "127.0.0.1"}}, f)
            cfg = self._load(path)
            self.assertTrue(cfg["enabled"])
            self.assertEqual(cfg["hosts"], ["127.0.0.1"])
        finally:
            os.unlink(path)

    def test_enabled_without_host_does_not_fill_defaults(self):
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w") as f:
                json.dump({"mqtt": {"enabled": True}}, f)
            cfg = self._load(path)
            self.assertTrue(cfg["enabled"])
            self.assertEqual(cfg["hosts"], [])
            self.assertNotIn("10.0.0.111", cfg["hosts"])
            self.assertNotIn("127.0.0.1", cfg["hosts"])
        finally:
            os.unlink(path)

    def test_mqtt_host_env_is_explicit(self):
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w") as f:
                json.dump({"mqtt": {"enabled": True}}, f)
            cfg = self._load(path, extra_env={"MQTT_HOST": "broker.internal"})
            self.assertEqual(cfg["hosts"], ["broker.internal"])
        finally:
            os.unlink(path)

    def test_publish_enabled_without_hosts_is_noop(self):
        with mock.patch.object(ha_mqtt, "_mqtt_connect_publish") as pub:
            ok, detail = ha_mqtt.publish_json(
                "example/vision/front_door", {"x": 1},
                cfg={"enabled": True, "hosts": [], "port": 1883},
            )
        self.assertFalse(ok)
        self.assertIn("no hosts", detail)
        pub.assert_not_called()


if __name__ == "__main__":
    unittest.main()
