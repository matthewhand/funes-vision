"""Configured `cameras[]` registry vs the legacy camera_kind() fallback."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analyze_images as ai


class CameraKindConfigTest(unittest.TestCase):
    def setUp(self):
        self._orig = ai.CAMERAS
        ai.CAMERAS = []

    def tearDown(self):
        ai.CAMERAS = self._orig

    def test_configured_kind_wins_over_filename(self):
        # The path names the configured dir, so the configured kind wins even
        # though the Hikvision filename says 10.0.0.21 (which legacy -> front).
        ai.CAMERAS = [
            {"id": "Webcam21", "label": "Front", "kind": "back",
             "dir": "/mnt/models/Webcam21"},
        ]
        self.assertEqual(
            ai.camera_kind("/mnt/models/Webcam21/10.0.0.21_01_x.jpg"), "back")

    def test_configured_kind_by_id_and_dir(self):
        ai.CAMERAS = [
            {"id": "GateCam", "kind": "back", "dir": "/data/gate"},
            {"id": "YardCam", "kind": "front", "dir": "/data/yard"},
        ]
        self.assertEqual(ai.camera_kind("/data/gate/10.0.0.21_x.jpg"), "back")
        self.assertEqual(ai.camera_kind("/data/yard/10.0.0.22_x.jpg"), "front")

    def test_source_alias_and_dog_kind(self):
        ai.CAMERAS = [{"id": "c1", "kind": "dog", "source": "/srv/cam1"}]
        self.assertEqual(ai.camera_kind("/srv/cam1/frame.jpg"), "back")

    def test_kind_aliases(self):
        self.assertEqual(ai.normalize_camera_kind("dog"), "back")
        self.assertEqual(ai.normalize_camera_kind("dog_cam"), "back")
        self.assertEqual(ai.normalize_camera_kind("other"), "front")
        self.assertEqual(ai.normalize_camera_kind("nonsense"), "front")
        self.assertEqual(ai.normalize_camera_kind(None), "front")

    def test_fallback_preserves_legacy_front(self):
        self.assertEqual(ai.camera_kind("10.0.0.21_01_20260618101522301_MOTDEC.jpg"), "front")
        self.assertEqual(ai.camera_kind("/mnt/models/Webcam21/x.jpg"), "front")

    def test_fallback_preserves_legacy_back(self):
        self.assertEqual(ai.camera_kind("10.0.0.22_01_20260618143028000_MOTDEC.jpg"), "back")
        self.assertEqual(ai.camera_kind("/mnt/models/Webcam22/x.jpg"), "back")

    def test_unmatched_path_defaults_front(self):
        ai.CAMERAS = [{"id": "c1", "kind": "back", "dir": "/srv/cam1"}]
        self.assertEqual(ai.camera_kind("/other/foo.jpg"), "front")

    def test_non_dict_entries_ignored(self):
        ai.CAMERAS = ["nonsense", {"id": "c1", "kind": "back", "dir": "/srv/cam1"}]
        self.assertEqual(ai.camera_kind("/srv/cam1/x.jpg"), "back")


if __name__ == "__main__":
    unittest.main()
