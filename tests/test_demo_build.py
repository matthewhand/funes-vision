"""Static demo bundle: fixtures only, no live camera paths."""
import http.client
import http.server
import json
import re
import shutil
import tempfile
import threading
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


# The Help button is <a class="btn" id="btn-help" href="USER-GUIDE.html" target="_blank">
# in index.html -- a live link, not a JS no-op, and nothing in the SPA reads
# window.__IS_DEMO__ to suppress it. The published demo shipped the page without
# the target, so the button 404'd there and on no other target. The one other
# guarantee here is provenance: everything the walkthrough pulls in has to come
# from docs/, never a live camera path.
HELP_TAG = re.compile(r"<a\b[^>]*\bid=[\"']btn-help[\"'][^>]*>", re.IGNORECASE)
HREF = re.compile(r"\bhref=[\"']([^\"']+)[\"']", re.IGNORECASE)
# A relative reference inside the guide: not a fragment, not remote, not inline.
GUIDE_ASSET = re.compile(r"\b(?:src|href)=[\"'](?!#|https?:|//|data:|mailto:)([^\"']+)[\"']")


class TestDemoHelpWalkthrough(unittest.TestCase):
    """The built bundle has to actually serve what #btn-help points at.

    Checked over HTTP against the real built directory rather than by scanning
    build_demo.py for the filename: the loose textual check in
    tests/test_static_asset_manifest.js would be satisfied by a comment or a
    dead string, and a build can name an asset and still copy it to the wrong
    path. Here the only thing that passes is a 200.
    """

    @classmethod
    def setUpClass(cls):
        import sys

        if str(ROOT / "tools" / "demo") not in sys.path:
            sys.path.insert(0, str(ROOT / "tools" / "demo"))
        from build_demo import build

        cls.tmp = Path(tempfile.mkdtemp(prefix="webcam_demo_help_"))
        cls.out = build(cls.tmp)
        cls.help_href = cls._help_href((cls.out / "index.html").read_text())
        handler = type(
            "QuietHandler",
            (http.server.SimpleHTTPRequestHandler,),
            {"log_message": lambda *a, **k: None},
        )
        cls.srv = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            lambda *a, **k: handler(*a, directory=str(cls.out), **k),
        )
        threading.Thread(target=cls.srv.serve_forever, kwargs={"poll_interval": 0.01},
                         daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def _help_href(html):
        tag = HELP_TAG.search(html)
        assert tag, "no <a id=btn-help> in the built index.html; this test cannot follow it"
        href = HREF.search(tag.group(0))
        assert href, f'#btn-help has no href: {tag.group(0)}'
        return href.group(1)

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_help_link_is_a_live_local_href(self):
        # Guard the guard: if this stopped matching, every assertion below would
        # pass against a 404 target rather than a served one.
        self.assertEqual(self.help_href, "USER-GUIDE.html")
        self.assertTrue(
            not re.match(r"^(?:[a-z]+:|//|/)", self.help_href),
            f"#btn-help must point at a same-root file, got {self.help_href}",
        )

    def test_help_link_resolves_over_http(self):
        status, body = self._get("/" + self.help_href)
        self.assertEqual(status, 200, f"{self.help_href} is not in the bundle")
        self.assertIn(b"Webcam gallery", body[:2000])

    def test_guide_is_the_repo_walkthrough(self):
        shipped = (self.out / "USER-GUIDE.html").read_bytes()
        self.assertEqual(shipped, (ROOT / "docs" / "USER-GUIDE.html").read_bytes())

    def test_every_asset_the_guide_pulls_in_ships(self):
        guide = (self.out / "USER-GUIDE.html").read_text()
        refs = sorted(set(GUIDE_ASSET.findall(guide)))
        # Guard the guard: the guide must still reference sibling files, or the
        # loop below would pass on an empty set.
        self.assertGreaterEqual(len(refs), 10, f"guide asset scan found only {refs}")
        bad = []
        for ref in refs:
            status, body = self._get("/" + ref)
            if status != 200 or not body:
                bad.append(f"{ref} -> HTTP {status} ({len(body)} bytes)")
        self.assertEqual(bad, [], f"walkthrough assets missing from the bundle: {bad}")

    def test_no_guide_asset_came_from_a_live_camera_path(self):
        for png in (self.out / "guide" / "img").glob("*"):
            self.assertNotIn("Webcam21", png.name)
            self.assertNotIn("Webcam22", png.name)
            self.assertEqual(
                png.read_bytes(), (ROOT / "docs" / "guide" / "img" / png.name).read_bytes()
            )


if __name__ == "__main__":
    unittest.main()
