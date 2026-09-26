"""HTTP-level tests for the destructive and streaming endpoints (#57).

Covers /api/delete (including path-traversal rejection and invalid input),
/api/clip, /api/events (SSE event framing), /api/catalogs, /api/llm-schema,
/api/inference_log and /api/integrations. The handler is driven directly,
matching tests/test_api_cameras.py; the mutation-auth check also runs against a
real loopback ThreadingHTTPServer so the 401 is observed over the wire.
"""
import http.client
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api_server


class FakeHandler:
    """Drive api_server.Handler with captured status/headers/body.

    Extends the harness in test_api_cameras.py so endpoints that write raw
    responses (clip, SSE) are observable too, not just the JSON _send path.
    """

    def __init__(self):
        self.sent = []
        self.status = None
        self.headers_sent = {}
        self.handler = api_server.Handler.__new__(api_server.Handler)

    def _send(self, code, obj):
        self.sent.append((code, obj))

    def _send_response(self, code, message=None):
        self.status = code

    def _send_header(self, key, value):
        self.headers_sent[key] = value

    def _end_headers(self):
        pass

    def run(self, method, path, body=b"", headers=None):
        h = self.handler
        self.sent = []
        self.status = None
        self.headers_sent = {}
        h._send = self._send
        h.send_response = self._send_response
        h.send_header = self._send_header
        h.end_headers = self._end_headers
        h.path = path
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
        return self.sent[-1] if self.sent else (self.status, None)

    @property
    def body(self):
        return self.handler.wfile.getvalue()


def parse_sse(raw):
    """[(event, decoded_data), ...] from a text/event-stream payload."""
    out = []
    for block in raw.decode().split("\n\n"):
        if not block.strip():
            continue
        event = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        out.append((event, data))
    return out


class FakeTime:
    """Deterministic clock so the SSE loop hits its heartbeat/idle branches
    without sleeping in real time. Step of 1 makes each monotonic() call see
    one more second than the last."""

    def __init__(self, step=1.0):
        self._t = 0.0
        self._step = step

    def monotonic(self):
        self._t += self._step
        return self._t

    def sleep(self, _):
        pass

    def time(self):
        return 1_000_000.0


class EndpointTestCase(unittest.TestCase):
    """Per-test camera roots in a temp dir; never touches /mnt/models."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="webcam_api_ep_")
        self.front = os.path.join(self.tmp, "front")
        self.back = os.path.join(self.tmp, "back")
        os.makedirs(self.front)
        os.makedirs(self.back)
        self._orig = {
            "WATCH_DIRS": api_server.WATCH_DIRS,
            "BASE_DIR": api_server.BASE_DIR,
            "SETTINGS_FILE": api_server.SETTINGS_FILE,
            "INTEGRATIONS_FILE": api_server.INTEGRATIONS_FILE,
        }
        api_server.WATCH_DIRS = [self.front, self.back]
        api_server.BASE_DIR = self.tmp
        api_server.SETTINGS_FILE = os.path.join(self.tmp, "settings.json")
        api_server.INTEGRATIONS_FILE = os.path.join(self.tmp, "integrations.json")
        # Pin auth off unless a test opts in, so a developer env token can't
        # turn a mutation test into a surprise 401.
        self._orig_token = os.environ.pop("WEBCAM_API_TOKEN", None)
        self.h = FakeHandler()

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(api_server, k, v)
        if self._orig_token is not None:
            os.environ["WEBCAM_API_TOKEN"] = self._orig_token
        else:
            os.environ.pop("WEBCAM_API_TOKEN", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_frame(self, name, camera=None, thumb=False):
        d = camera or self.front
        with open(os.path.join(d, name), "w") as f:
            f.write("x")
        if thumb:
            os.makedirs(os.path.join(d, "thumbs"), exist_ok=True)
            with open(os.path.join(d, "thumbs", name), "w") as f:
                f.write("t")
        return os.path.join(d, name)


class TestDelete(EndpointTestCase):
    def test_delete_removes_image_and_thumb(self):
        frame = self.add_frame("delete_me.jpg", thumb=True)
        thumb = os.path.join(self.front, "thumbs", "delete_me.jpg")
        code, body = self.h.run(
            "POST", "/api/delete?camera=front",
            json.dumps({"filename": "delete_me.jpg"}).encode())
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(os.path.exists(frame))
        self.assertFalse(os.path.exists(thumb))

    def test_delete_removes_pin(self):
        self.add_frame("pinned.jpg")
        with open(os.path.join(self.front, "pins.json"), "w") as f:
            json.dump(["pinned.jpg"], f)
        code, _ = self.h.run(
            "POST", "/api/delete?camera=front",
            json.dumps({"filename": "pinned.jpg"}).encode())
        self.assertEqual(code, 200)
        with open(os.path.join(self.front, "pins.json")) as f:
            self.assertNotIn("pinned.jpg", json.load(f))

    def test_delete_path_traversal_rejected(self):
        outside = os.path.join(self.tmp, "outside.jpg")
        with open(outside, "w") as f:
            f.write("secret")
        self.add_frame("front_0.jpg")
        for bad in ("../outside.jpg", "../../etc/passwd", "/etc/passwd",
                    "thumbs/../outside.jpg"):
            code, _ = self.h.run(
                "POST", "/api/delete?camera=front",
                json.dumps({"filename": bad}).encode())
            self.assertEqual(code, 404, bad)
        self.assertTrue(os.path.exists(outside))

    def test_delete_cross_camera_is_404(self):
        frame = self.add_frame("back_only.jpg", camera=self.back)
        code, _ = self.h.run(
            "POST", "/api/delete?camera=front",
            json.dumps({"filename": "back_only.jpg"}).encode())
        self.assertEqual(code, 404)
        self.assertTrue(os.path.exists(frame))

    def test_delete_requires_camera(self):
        frame = self.add_frame("x.jpg")
        code, body = self.h.run(
            "POST", "/api/delete", json.dumps({"filename": "x.jpg"}).encode())
        self.assertEqual(code, 400)
        self.assertIn("camera", body["error"])
        self.assertTrue(os.path.exists(frame))

    def test_delete_missing_file_404(self):
        code, _ = self.h.run(
            "POST", "/api/delete?camera=front",
            json.dumps({"filename": "nope.jpg"}).encode())
        self.assertEqual(code, 404)

    def test_delete_bad_json_400(self):
        code, _ = self.h.run("POST", "/api/delete?camera=front", b"{not json")
        self.assertEqual(code, 400)

    def test_delete_hidden_filename_rejected(self):
        frame = self.add_frame(".secret.jpg")
        code, _ = self.h.run(
            "POST", "/api/delete?camera=front",
            json.dumps({"filename": ".secret.jpg"}).encode())
        self.assertEqual(code, 404)
        self.assertTrue(os.path.exists(frame))


class TestClip(EndpointTestCase):
    @staticmethod
    def _fake_build(payload=b"GIF89a-body", record=None):
        def build(frames, out_path):
            if record is not None:
                record.append(frames)
            with open(out_path, "wb") as f:
                f.write(payload)
            return True
        return build

    def test_clip_requires_files(self):
        for payload in ({}, {"files": []}, {"files": "x.jpg"}):
            code, _ = self.h.run("POST", "/api/clip",
                                 json.dumps(payload).encode())
            self.assertEqual(code, 400, payload)

    def test_clip_rejects_traversal_and_missing(self):
        code, _ = self.h.run(
            "POST", "/api/clip",
            json.dumps({"files": ["../etc/passwd", "nope.jpg"]}).encode())
        self.assertEqual(code, 404)

    def test_clip_gif(self):
        self.add_frame("clip_a.jpg")
        self.add_frame("clip_b.jpg")
        import integrations.media as media
        orig = media.build_gif
        media.build_gif = self._fake_build(b"GIF89a-body")
        try:
            code, body = self.h.run(
                "POST", "/api/clip",
                json.dumps({"files": ["clip_a.jpg", "clip_b.jpg"]}).encode())
        finally:
            media.build_gif = orig
        self.assertEqual(code, 200)
        self.assertEqual(self.h.headers_sent.get("Content-Type"), "image/gif")
        self.assertEqual(self.h.body, b"GIF89a-body")
        self.assertTrue(self.h.headers_sent.get(
            "Content-Disposition", "").endswith('filename="visit.gif"'))

    def test_clip_mp4(self):
        self.add_frame("clip_v.mp4.jpg")
        import integrations.media as media
        orig = media.build_mp4
        media.build_mp4 = self._fake_build(b"MP4-body")
        try:
            code, _ = self.h.run(
                "POST", "/api/clip",
                json.dumps({"files": ["clip_v.mp4.jpg"],
                            "format": "mp4"}).encode())
        finally:
            media.build_mp4 = orig
        self.assertEqual(code, 200)
        self.assertEqual(self.h.headers_sent.get("Content-Type"), "video/mp4")
        self.assertEqual(self.h.body, b"MP4-body")
        self.assertTrue(self.h.headers_sent.get(
            "Content-Disposition", "").endswith('filename="visit.mp4"'))

    def test_clip_drops_unresolvable_keeps_valid(self):
        self.add_frame("clip_ok.jpg")
        import integrations.media as media
        seen = []
        orig = media.build_gif
        media.build_gif = self._fake_build(record=seen)
        try:
            code, _ = self.h.run(
                "POST", "/api/clip",
                json.dumps({"files": ["missing.jpg", "clip_ok.jpg"]}).encode())
        finally:
            media.build_gif = orig
        self.assertEqual(code, 200)
        self.assertEqual(seen[0], [os.path.join(self.front, "clip_ok.jpg")])

    def test_clip_build_failure_500(self):
        self.add_frame("clip_fail.jpg")
        import integrations.media as media
        orig = media.build_gif
        media.build_gif = lambda frames, out_path: False
        try:
            code, _ = self.h.run(
                "POST", "/api/clip",
                json.dumps({"files": ["clip_fail.jpg"]}).encode())
        finally:
            media.build_gif = orig
        self.assertEqual(code, 500)


class TestEvents(EndpointTestCase):
    def _stream(self):
        orig = api_server.time
        api_server.time = FakeTime()
        os.environ["WEBCAM_SSE_HEARTBEAT_S"] = "1"
        os.environ["WEBCAM_SSE_IDLE_TIMEOUT_S"] = "2"
        try:
            return self.h.run("GET", "/api/events")
        finally:
            api_server.time = orig
            os.environ.pop("WEBCAM_SSE_HEARTBEAT_S", None)
            os.environ.pop("WEBCAM_SSE_IDLE_TIMEOUT_S", None)

    def test_events_sets_stream_headers(self):
        self._stream()
        self.assertEqual(self.h.status, 200)
        self.assertEqual(self.h.headers_sent.get("Content-Type"),
                         "text/event-stream")
        self.assertEqual(self.h.headers_sent.get("Cache-Control"), "no-cache")
        self.assertEqual(self.h.headers_sent.get("X-Accel-Buffering"), "no")

    def test_events_frames_are_well_formed(self):
        with open(os.path.join(self.tmp, "analysis.json"), "w") as f:
            json.dump({"front_0.jpg": {"person": True}}, f)
        self._stream()
        frames = parse_sse(self.h.body)
        names = [event for event, _ in frames]
        self.assertIn("ping", names)
        self.assertIn("close", names)
        for event, data in frames:
            self.assertTrue(event)
            self.assertIsInstance(data, dict)
        close = [data for event, data in frames if event == "close"][0]
        self.assertEqual(close["reason"], "idle_timeout")

    def test_events_releases_stream_slot(self):
        self._stream()
        self.assertEqual(api_server.sse_active(), 0)


class TestCatalogs(EndpointTestCase):
    def _write_catalogs(self, d, images):
        with open(os.path.join(d, "images.json"), "w") as f:
            json.dump(images, f)
        with open(os.path.join(d, "analysis.json"), "w") as f:
            json.dump({"frame_0.jpg": {"person": True}}, f)
        with open(os.path.join(d, "bursts.json"), "w") as f:
            json.dump({"b1": {"summary": "x"}}, f)
        with open(os.path.join(d, "pins.json"), "w") as f:
            json.dump(["frame_0.jpg"], f)

    def test_catalogs_single_camera(self):
        self._write_catalogs(self.front, ["front_0.jpg"])
        code, body = self.h.run("GET", "/api/catalogs?camera=front")
        self.assertEqual(code, 200)
        self.assertEqual(body["images"], ["front_0.jpg"])
        self.assertEqual(body["pins"], ["frame_0.jpg"])
        self.assertIn("analysis", body)
        self.assertIn("bursts", body)

    def test_catalogs_thumb_url(self):
        self._write_catalogs(self.front, ["front_0.jpg"])
        code, body = self.h.run("GET", "/api/catalogs?camera=front")
        self.assertEqual(body["thumbUrl"], "/cameras/front/thumbs/front_0.jpg")

    def test_catalogs_all_cameras(self):
        self._write_catalogs(self.front, ["front_0.jpg"])
        self._write_catalogs(self.back, ["back_0.jpg"])
        code, body = self.h.run("GET", "/api/catalogs")
        self.assertEqual(code, 200)
        self.assertEqual(set(body), {"front", "back"})
        self.assertEqual(body["back"]["thumbUrl"],
                         "/cameras/back/thumbs/back_0.jpg")

    def test_catalogs_missing_files_are_empty_defaults(self):
        code, body = self.h.run("GET", "/api/catalogs?camera=front")
        self.assertEqual(code, 200)
        self.assertEqual(body["images"], [])
        self.assertEqual(body["pins"], [])
        self.assertEqual(body["analysis"], {})
        self.assertEqual(body["bursts"], {})
        self.assertIsNone(body["thumbUrl"])

    def test_catalogs_unknown_camera_400(self):
        code, body = self.h.run("GET", "/api/catalogs?camera=nope")
        self.assertEqual(code, 400)
        self.assertIn("nope", body["error"])


class TestUnknownCameraPost(EndpointTestCase):
    """#100: an unknown ?camera= must be rejected once, at the top of do_POST.

    _camera_param sends the 400 itself, so do_POST has to return immediately —
    otherwise execution falls through and writes a second response.
    """

    def test_unknown_camera_single_400_for_each_write_endpoint(self):
        self.add_frame("x.jpg")
        for path in ("/api/delete", "/api/clip", "/api/pin"):
            self.h.run(
                "POST", path + "?camera=nope",
                json.dumps({"filename": "x.jpg"}).encode())
            codes = [code for code, _ in self.h.sent]
            self.assertEqual(codes, [400], f"{path} wrote {codes}")

    def test_unknown_camera_400_reports_the_id(self):
        self.add_frame("x.jpg")
        code, body = self.h.run(
            "POST", "/api/delete?camera=nope",
            json.dumps({"filename": "x.jpg"}).encode())
        self.assertEqual(code, 400)
        self.assertIn("nope", body["error"])


class TestReadEndpoints(EndpointTestCase):
    def test_llm_schema(self):
        code, body = self.h.run("GET", "/api/llm-schema")
        self.assertEqual(code, 200)
        self.assertIn("prompt", body)
        self.assertIn("schemas", body)

    def test_inference_log_newest_first_and_capped(self):
        entries = [{"started": i} for i in range(60)]
        with open(os.path.join(self.tmp, "inference_log.json"), "w") as f:
            json.dump(entries, f)
        code, body = self.h.run("GET", "/api/inference_log")
        self.assertEqual(code, 200)
        self.assertEqual(len(body), 50)
        self.assertEqual(body[0]["started"], 59)
        self.assertEqual(body[-1]["started"], 10)

    def test_inference_log_missing_is_empty(self):
        code, body = self.h.run("GET", "/api/inference_log")
        self.assertEqual(code, 200)
        self.assertEqual(body, [])


class TestIntegrations(EndpointTestCase):
    def _write(self, obj):
        with open(api_server.INTEGRATIONS_FILE, "w") as f:
            json.dump(obj, f)

    def test_get_missing_is_redacted(self):
        code, body = self.h.run("GET", "/api/integrations")
        self.assertEqual(code, 200)
        self.assertFalse(body["slack"]["has_bot_token"])
        self.assertEqual(body["slack"]["notify_mode"], "context")

    def test_post_saves_and_redacts_secret(self):
        payload = {"slack": {
            "enabled": True,
            "bot_token": "s3cr3t",
            "app_token": "app-tok",
            "channel_id": "C1",
            "public_base_url": "http://gallery.test",
            "notify_mode": "objects",
        }}
        code, body = self.h.run("POST", "/api/integrations",
                                json.dumps(payload).encode())
        self.assertEqual(code, 200)
        self.assertTrue(body["slack"]["has_bot_token"])
        self.assertTrue(body["slack"]["has_app_token"])
        self.assertNotIn("s3cr3t", json.dumps(body))
        with open(api_server.INTEGRATIONS_FILE) as f:
            saved = json.load(f)
        self.assertEqual(saved["slack"]["bot_token"], "s3cr3t")
        self.assertEqual(saved["slack"]["notify_mode"], "objects")
        self.assertEqual(
            stat.S_IMODE(os.stat(api_server.INTEGRATIONS_FILE).st_mode), 0o600)

    def test_post_blank_token_preserves_secret(self):
        self._write({"slack": {"bot_token": "keepme", "channel_id": "C0"}})
        code, body = self.h.run("POST", "/api/integrations",
                                json.dumps({"slack": {"channel_id": "C1"}}).encode())
        self.assertEqual(code, 200)
        with open(api_server.INTEGRATIONS_FILE) as f:
            saved = json.load(f)
        self.assertEqual(saved["slack"]["bot_token"], "keepme")
        self.assertEqual(saved["slack"]["channel_id"], "C1")
        self.assertTrue(body["slack"]["has_bot_token"])
        self.assertNotIn("keepme", json.dumps(body))

    def test_post_invalid_notify_mode_400(self):
        code, body = self.h.run(
            "POST", "/api/integrations",
            json.dumps({"slack": {"notify_mode": "bogus"}}).encode())
        self.assertEqual(code, 400)
        self.assertIn("notify_mode", body["error"])

    def test_post_invalid_base_url_400(self):
        code, _ = self.h.run(
            "POST", "/api/integrations",
            json.dumps({"slack": {"public_base_url": "gallery.test"}}).encode())
        self.assertEqual(code, 400)

    def test_post_empty_payload_400(self):
        code, _ = self.h.run("POST", "/api/integrations", json.dumps({}).encode())
        self.assertEqual(code, 400)

    def test_get_corrupt_409(self):
        with open(api_server.INTEGRATIONS_FILE, "w") as f:
            f.write("{not json")
        code, body = self.h.run("GET", "/api/integrations")
        self.assertEqual(code, 409)
        self.assertTrue(body.get("unreadable"))

    def test_post_corrupt_409_preserves_file(self):
        with open(api_server.INTEGRATIONS_FILE, "w") as f:
            f.write("{not json")
        code, _ = self.h.run(
            "POST", "/api/integrations",
            json.dumps({"slack": {"channel_id": "C1"}}).encode())
        self.assertEqual(code, 409)
        with open(api_server.INTEGRATIONS_FILE) as f:
            self.assertEqual(f.read(), "{not json")


class TestMutationAuthHTTP(unittest.TestCase):
    """/api/delete over a real loopback socket: #92's token gate at HTTP level."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="webcam_api_auth_")
        self.front = os.path.join(self.tmp, "front")
        os.makedirs(self.front)
        self._orig_dirs = api_server.WATCH_DIRS
        self._orig_settings = api_server.SETTINGS_FILE
        api_server.WATCH_DIRS = [self.front]
        api_server.SETTINGS_FILE = os.path.join(self.tmp, "settings.json")
        self._orig_token = os.environ.get("WEBCAM_API_TOKEN")
        os.environ["WEBCAM_API_TOKEN"] = "s3cret"
        self.srv = api_server.ThreadingHTTPServer(("127.0.0.1", 0),
                                                  api_server.Handler)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=2)
        api_server.WATCH_DIRS = self._orig_dirs
        api_server.SETTINGS_FILE = self._orig_settings
        if self._orig_token is None:
            os.environ.pop("WEBCAM_API_TOKEN", None)
        else:
            os.environ["WEBCAM_API_TOKEN"] = self._orig_token
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post(self, path, obj, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        conn.request("POST", path, body=json.dumps(obj).encode(), headers=hdrs)
        resp = conn.getresponse()
        resp.read()
        status = resp.status
        conn.close()
        return status

    def _frame(self, name="victim.jpg"):
        path = os.path.join(self.front, name)
        with open(path, "w") as f:
            f.write("x")
        return path

    def test_unauthenticated_delete_rejected(self):
        frame = self._frame()
        status = self._post("/api/delete?camera=front", {"filename": "victim.jpg"})
        self.assertEqual(status, 401)
        self.assertTrue(os.path.exists(frame))

    def test_wrong_token_rejected(self):
        frame = self._frame()
        status = self._post("/api/delete?camera=front", {"filename": "victim.jpg"},
                            {"Authorization": "Bearer nope"})
        self.assertEqual(status, 401)
        self.assertTrue(os.path.exists(frame))

    def test_bearer_token_allows_delete(self):
        frame = self._frame()
        status = self._post("/api/delete?camera=front", {"filename": "victim.jpg"},
                            {"Authorization": "Bearer s3cret"})
        self.assertEqual(status, 200)
        self.assertFalse(os.path.exists(frame))


if __name__ == "__main__":
    unittest.main()
