"""Screenshot proxy must never serve live camera roots."""
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY = os.path.join(HERE, "tools", "screenshots", "proxy.py")


def load_proxy():
    spec = importlib.util.spec_from_file_location("webcam_shot_proxy", PROXY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestScreenshotProxy(unittest.TestCase):
    def test_forbids_live_camera_roots(self):
        p = load_proxy()
        self.assertTrue(p._is_forbidden("/mnt/models/Webcam21"))
        self.assertTrue(p._is_forbidden("/mnt/models/Webcam22/thumbs"))
        self.assertFalse(p._is_forbidden(p.DEFAULT_ROOT))
        self.assertFalse(p._is_forbidden(os.path.join(HERE, "tools/screenshots/fixtures/gallery")))

    def test_default_root_is_fixture_gallery(self):
        p = load_proxy()
        self.assertTrue(p.DEFAULT_ROOT.endswith("fixtures/gallery"))
        self.assertNotIn("/mnt/models/", p.DEFAULT_ROOT)


if __name__ == "__main__":
    unittest.main()
