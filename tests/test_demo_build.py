"""Static demo bundle: fixtures only, no live camera paths."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestDemoBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(ROOT / "tools" / "demo"))
        from build_demo import build
        cls.tmp = Path(tempfile.mkdtemp(prefix="webcam_demo_"))
        cls.out = build(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_sentinel_and_noindex(self):
        html = (self.out / "index.html").read_text()
        self.assertIn("window.__IS_DEMO__=true", html)
        self.assertIn("noindex,nofollow", html)
        self.assertIn("demo-shim.js", html)
        self.assertIn("canonical", html)

    def test_no_live_camera_dir_copied(self):
        text = str(self.out)
        self.assertNotIn("/mnt/models", text)
        for p in self.out.rglob("*"):
            self.assertNotIn("Webcam21", p.name)
            self.assertNotIn("Webcam22", p.name)

    def test_network_has_status_and_catalogs(self):
        net = json.loads((self.out / "fixtures" / "network.json").read_text())
        static = net["static"]
        self.assertIn("/api/status", static)
        self.assertIn("/api/cameras", static)
        self.assertIn("/api/catalogs?camera=Webcam21", static)
        cams = static["/api/cameras"]["body"]
        self.assertEqual(cams[0]["id"], "Webcam21")
        cat = static["/api/catalogs?camera=Webcam21"]["body"]
        self.assertTrue(cat["images"])
        self.assertTrue(all(n.startswith("10.0.0.21_") for n in cat["images"]))

    def test_stills_are_present_and_tiny(self):
        jpgs = list(self.out.glob("*.jpg"))
        self.assertGreaterEqual(len(jpgs), 8)
        thumbs = list((self.out / "thumbs").glob("*.jpg"))
        self.assertEqual(len(jpgs), len(thumbs))
        self.assertTrue((self.out / "images.json").is_file())
        self.assertTrue((self.out / "analysis.json").is_file())

    def test_shim_passthrough_and_eventsource(self):
        shim = (self.out / "static" / "demo-shim.js").read_text()
        self.assertIn("EventSource", shim)
        self.assertIn("jpg|jpeg", shim)
        self.assertIn("pathQ", shim)


if __name__ == "__main__":
    unittest.main()
