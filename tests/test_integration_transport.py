"""Transport-level tests for the Slack and ntfy integrations.

These drive the real ``requests`` calls against a throwaway stdlib HTTP server
on 127.0.0.1:0 instead of monkeypatching ``requests.post``, so the assertions
cover the HTTP method, URL, auth header and encoded payload the providers
actually put on the wire.
"""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import integrations
from integrations import ntfy, slack


class _StubHandler(BaseHTTPRequestHandler):
    server_version = "StubHTTP/1.0"

    def log_message(self, *args):
        pass

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        rec = {
            "method": self.command,
            "path": self.path.split("?")[0],
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": body,
        }
        self.server.requests.append(rec)
        return rec

    def _respond(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200):
        self._respond(status, json.dumps(obj).encode(), "application/json")

    def do_POST(self):
        rec = self._record()
        forced = getattr(self.server, "force_status", None)
        if forced:
            self._respond(forced, b"forced", "text/plain")
            return
        path = rec["path"]
        if path.endswith("chat.postMessage") or path.endswith("chat.update"):
            self._json({"ok": True, "ts": "1700000000.000200"} if path.endswith("postMessage")
                       else {"ok": True, "ts": "1699999999.000001"})
        elif path.endswith("files.getUploadURLExternal"):
            if getattr(self.server, "fail_upload", False):
                self._json({"ok": False, "error": "missing_scope"})
                return
            port = self.server.server_address[1]
            self._json({"ok": True, "file_id": "F1",
                        "upload_url": f"http://127.0.0.1:{port}/upload/F1"})
        elif path.endswith("files.completeUploadExternal"):
            self._json({"ok": True})
        else:
            self._respond(200, b"ok", "text/plain")


class IntegrationTransportTestCase(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        self.httpd.requests = []
        self.httpd.force_status = None
        self.httpd.fail_upload = False
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    @property
    def requests(self):
        return self.httpd.requests

    def _matching(self, suffix):
        return [r for r in self.requests if r["path"].endswith(suffix)]

    def _one(self, suffix):
        found = self._matching(suffix)
        self.assertEqual(len(found), 1, f"expected one {suffix} request, got {self.requests}")
        return found[0]


class TestSlackTransport(IntegrationTransportTestCase):
    def setUp(self):
        super().setUp()
        self._api = slack.API
        slack.API = f"{self.base}/api"
        self.addCleanup(setattr, slack, "API", self._api)

    def test_send_message_posts_bearer_and_json(self):
        cfg = {"bot_token": "xoxb-test", "channel_id": "C123"}
        ok, detail, ts = slack.send_message(cfg, "hello world")
        self.assertTrue(ok)
        self.assertEqual(ts, "1700000000.000200")
        req = self._one("chat.postMessage")
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["headers"]["authorization"], "Bearer xoxb-test")
        self.assertEqual(json.loads(req["body"]),
                         {"channel": "C123", "text": "hello world"})
        self.assertEqual(len(self.requests), 1)

    def test_send_message_update_edits_existing_ts(self):
        cfg = {"bot_token": "xoxb-test", "channel_id": "C123"}
        ok, _detail, ts = slack.send_message(cfg, "recovered", update_ts="1699999999.000001")
        self.assertTrue(ok)
        self.assertEqual(ts, "1699999999.000001")
        req = self._one("chat.update")
        self.assertEqual(json.loads(req["body"]),
                         {"channel": "C123", "ts": "1699999999.000001", "text": "recovered"})
        self.assertEqual(req["headers"]["authorization"], "Bearer xoxb-test")

    def test_post_image_uploads_then_completes(self):
        fd, path = tempfile.mkstemp(suffix=".jpg")
        self.addCleanup(os.remove, path)
        with os.fdopen(fd, "wb") as f:
            f.write(b"\xff\xd8fake-jpeg\xff\xd9")
        size = os.path.getsize(path)
        cfg = {"bot_token": "xoxb-test", "channel_id": "C123",
               "public_base_url": "https://cam.example/"}

        ok, detail = slack.post_image(cfg, "frame-1.jpg", ["person"], "A person", path)
        self.assertTrue(ok)

        init = self._one("files.getUploadURLExternal")
        self.assertEqual(init["method"], "POST")
        self.assertEqual(init["headers"]["authorization"], "Bearer xoxb-test")
        form = parse_qs(init["body"].decode())
        self.assertEqual(form["filename"], [os.path.basename(path)])
        self.assertEqual(form["length"], [str(size)])

        upload = self._one("/upload/F1")
        self.assertTrue(upload["headers"]["content-type"].startswith("multipart/form-data"))

        done = self._one("files.completeUploadExternal")
        self.assertEqual(done["headers"]["authorization"], "Bearer xoxb-test")
        done_form = parse_qs(done["body"].decode())
        self.assertEqual(json.loads(done_form["files"][0]),
                         [{"id": "F1", "title": "frame-1.jpg"}])
        self.assertEqual(done_form["channel_id"], ["C123"])
        self.assertIn("A person", done_form["initial_comment"][0])
        self.assertIn("https://cam.example/?image=frame-1.jpg", done_form["initial_comment"][0])

    def test_post_image_falls_back_to_text_when_upload_rejected(self):
        self.httpd.fail_upload = True
        fd, path = tempfile.mkstemp(suffix=".jpg")
        self.addCleanup(os.remove, path)
        with os.fdopen(fd, "wb") as f:
            f.write(b"\xff\xd8fake\xff\xd9")
        cfg = {"bot_token": "xoxb-test", "channel_id": "C123",
               "public_base_url": "https://cam.example"}

        ok, detail = slack.post_image(cfg, "frame-1.jpg", [], "caption", path)
        self.assertTrue(ok)
        self.assertTrue(detail.startswith("posted text-only"), detail)
        self.assertFalse(self._matching("files.completeUploadExternal"))
        posted = self._one("chat.postMessage")
        self.assertIn("no objects", json.loads(posted["body"])["text"])

    def test_post_burst_without_frames_posts_summary_and_link(self):
        cfg = {"bot_token": "xoxb-test", "channel_id": "C123",
               "public_base_url": "https://cam.example/"}
        ok, _detail = slack.post_burst(cfg, "burst-7", "someone walked up", [])
        self.assertTrue(ok)
        req = self._one("chat.postMessage")
        text = json.loads(req["body"])["text"]
        self.assertIn("someone walked up", text)
        self.assertIn("https://cam.example/?event=burst-7", text)
        self.assertEqual(req["headers"]["authorization"], "Bearer xoxb-test")

    def test_missing_credentials_short_circuits(self):
        ok, detail = slack.post_burst({}, "b", "s", [])
        self.assertFalse(ok)
        self.assertEqual(detail, "bot_token and channel_id are required")
        self.assertEqual(self.requests, [])


class TestNtfyTransport(IntegrationTransportTestCase):
    def test_post_burst_payload_auth_and_headers(self):
        cfg = {"server_url": self.base + "/", "topic": "/alerts", "token": "tk-123",
               "public_base_url": "https://cam.example/"}
        ok, detail = ntfy.post_burst(cfg, "b1", "the summary", [])
        self.assertTrue(ok, detail)
        self.assertEqual(detail, "sent")
        req = self._one("/alerts")
        self.assertEqual(req["method"], "POST")
        self.assertEqual(req["body"], b"the summary")
        self.assertEqual(req["headers"]["title"], "Webcam: sequence")
        self.assertEqual(req["headers"]["tags"], "movie_camera")
        self.assertEqual(req["headers"]["click"], "https://cam.example/?event=b1")
        self.assertEqual(req["headers"]["authorization"], "Bearer tk-123")

    def test_send_message_has_no_auth_without_token(self):
        cfg = {"server_url": self.base, "topic": "alerts"}
        ok, _detail, ts = ntfy.send_message(cfg, "disk low")
        self.assertTrue(ok)
        self.assertIsNone(ts)
        req = self._one("/alerts")
        self.assertEqual(req["body"], b"disk low")
        self.assertEqual(req["headers"]["title"], "Webcam")
        self.assertEqual(req["headers"]["tags"], "warning")
        self.assertNotIn("authorization", req["headers"])

    def test_post_image_click_through_and_title(self):
        cfg = {"server_url": self.base, "topic": "alerts",
               "public_base_url": "https://cam.example"}
        ok, _detail = ntfy.post_image(cfg, "shot.jpg", ["dog", "person"], "caption", None)
        self.assertTrue(ok)
        req = self._one("/alerts")
        self.assertEqual(req["headers"]["title"], "Webcam: dog, person")
        self.assertEqual(req["headers"]["tags"], "camera")
        self.assertEqual(req["headers"]["click"], "https://cam.example/?image=shot.jpg")
        self.assertEqual(req["body"], b"caption")

    def test_missing_config_never_hits_network(self):
        ok, detail = ntfy.post_burst({"server_url": self.base}, "b", "s", [])
        self.assertFalse(ok)
        self.assertEqual(detail, "topic is required")
        ok, detail = ntfy.post_burst({"topic": "alerts"}, "b", "s", [])
        self.assertFalse(ok)
        self.assertEqual(detail, "server_url is required")
        self.assertEqual(self.requests, [])

    def test_http_error_is_reported_not_raised(self):
        self.httpd.force_status = 503
        cfg = {"server_url": self.base, "topic": "alerts"}
        ok, detail, _ts = ntfy.send_message(cfg, "x")
        self.assertFalse(ok)
        self.assertEqual(detail, "HTTP 503")


class TestNotifyBurstFanout(IntegrationTransportTestCase):
    def test_notify_burst_dispatches_to_real_ntfy_transport(self):
        cfg_file = os.path.join(tempfile.mkdtemp(), "integrations.json")
        self.addCleanup(os.remove, cfg_file)
        with open(cfg_file, "w") as f:
            json.dump({"ntfy": {"enabled": True, "notify_mode": "context",
                                "server_url": self.base, "topic": "alerts",
                                "public_base_url": "https://cam.example"}}, f)
        self._config = integrations.CONFIG_FILE
        self._state = integrations.STATE_FILE
        integrations.CONFIG_FILE = cfg_file
        integrations.STATE_FILE = os.path.join(os.path.dirname(cfg_file), "state.json")
        self.addCleanup(setattr, integrations, "CONFIG_FILE", self._config)
        self.addCleanup(setattr, integrations, "STATE_FILE", self._state)

        integrations.notify_burst("burst-9", "context summary", [])
        req = self._one("/alerts")
        self.assertEqual(req["body"], b"context summary")
        self.assertEqual(req["headers"]["title"], "Webcam: sequence")
        self.assertEqual(req["headers"]["click"], "https://cam.example/?event=burst-9")


if __name__ == "__main__":
    unittest.main()
