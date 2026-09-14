import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, "/home/user/webcam")
import api_server


class FakeHandler:
    """Drive api_server.Handler.do_GET / do_POST with captured output."""
    def __init__(self):
        self.sent = []
        self.handler = api_server.Handler.__new__(api_server.Handler)

    def _send(self, code, body):
        self.sent.append((code, body))

    def run(self, method, path, body=b"", headers=None):
        h = self.handler
        h._send = self._send
        h.path = path
        # do_POST reads Content-Length off the headers to size the body read,
        # so a missing header makes it read nothing and dispatch on an empty
        # payload — set it explicitly.
        hdrs = {"Content-Length": str(len(body))}
        if headers:
            hdrs.update(headers)
        h.headers = hdrs
        h.rfile = io.BytesIO(body)
        h.wfile = io.BytesIO()
        if method == "GET":
            h.do_GET()
        else:
            h.do_POST()
        return self.sent[-1] if self.sent else (None, None)


class TestCameraAPI(unittest.TestCase):
    tmp = None

    @classmethod
    def setUpClass(cls):
        # Point the API at throwaway camera dirs so the test never touches the
        # live web roots or the pipeline's catalogs. Each camera gets a
        # uniquely-named file so the cross-camera 404 test is meaningful —
        # `find_image` without a camera_id returns the first match, so a
        # shared name would silently resolve to the wrong camera.
        cls.tmp = tempfile.mkdtemp(prefix="webcam_api_test_")
        cls.front = os.path.join(cls.tmp, "front")
        cls.back = os.path.join(cls.tmp, "back")
        for d, tag in ((cls.front, "front"), (cls.back, "back")):
            os.makedirs(d)
            for i in range(3):
                open(os.path.join(d, f"{tag}_{i}.jpg"), "w").write("x")
        cls._orig_dirs = api_server.WATCH_DIRS
        api_server.WATCH_DIRS = [cls.front, cls.back]

    @classmethod
    def tearDownClass(cls):
        api_server.WATCH_DIRS = cls._orig_dirs
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.h = FakeHandler()

    def test_cameras_endpoint(self):
        code, body = self.h.run("GET", "/api/cameras")
        self.assertEqual(code, 200)
        self.assertEqual([c["id"] for c in body], ["front", "back"])

    def test_pins_no_camera_returns_all(self):
        # No ?camera= means "all cameras" (backward compatible: the old
        # single-file API returned everything). The union must include the
        # pin we just wrote to the back camera.
        target = "back_0.jpg"
        self.h.run("POST", "/api/pin?camera=back",
                   json.dumps({"filename": target, "pinned": True}).encode())
        code, body = self.h.run("GET", "/api/pins")
        self.assertEqual(code, 200)
        self.assertIn(target, body)

    def test_pins_unknown_camera_is_400(self):
        code, body = self.h.run("GET", "/api/pins?camera=nope")
        self.assertEqual(code, 400)
        self.assertIn("nope", body["error"])

    def test_pin_requires_camera(self):
        # Writing to "all cameras" is not a thing — the old single-file API
        # silently did it and synced the pin into every web root.
        code, body = self.h.run("POST", "/api/pin",
                                json.dumps({"filename": "frame_0.jpg",
                                            "pinned": True}).encode())
        self.assertEqual(code, 400)

    def test_pin_scopes_to_requested_camera(self):
        target = "back_0.jpg"
        code, _ = self.h.run("POST", "/api/pin?camera=back",
                             json.dumps({"filename": target, "pinned": True}).encode())
        self.assertEqual(code, 200)
        # The pin lands in the back camera's pins.json only. The front camera
        # must have no pins.json at all — the old single-file API synced one
        # pins.json into every web root, which is the leak this fixes.
        self.assertTrue(os.path.exists(os.path.join(self.back, "pins.json")))
        back_pins = json.load(open(os.path.join(self.back, "pins.json")))
        self.assertIn(target, back_pins)
        self.assertFalse(os.path.exists(os.path.join(self.front, "pins.json")))

    def test_pin_cross_camera_is_404(self):
        # A file that lives in the front dir, pinned via ?camera=back, must 404
        # — pinning is per-camera and the file is not in the back camera's dir.
        code, body = self.h.run("POST", "/api/pin?camera=back",
                                json.dumps({"filename": "front_0.jpg",
                                            "pinned": True}).encode())
        self.assertEqual(code, 404)

    def test_status_scopes_cameras(self):
        # No ?camera= returns every camera (backward compatible). An explicit
        # id returns only that camera's row.
        code, all_status = self.h.run("GET", "/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(len(all_status["cameras"]), 2)
        code, scoped = self.h.run("GET", "/api/status?camera=back")
        self.assertEqual(code, 200)
        self.assertEqual(len(scoped["cameras"]), 1)
        self.assertEqual(scoped["cameras"][0]["name"], "back")

    def test_settings_lists_cameras(self):
        code, body = self.h.run("GET", "/api/settings")
        self.assertEqual(code, 200)
        self.assertIn("cameras", body)