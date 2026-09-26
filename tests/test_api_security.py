"""Security/resource-limit tests for api_server (#44, #71).

Covers the optional mutation token, the CORS allowlist (no wildcard), and the
concurrent-SSE cap. Socket-free: the handler is driven directly, matching the
style of test_api_cameras.py.
"""
import base64
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


class HeaderRecorder:
    """Stand-in for BaseHTTPRequestHandler's send_* methods."""

    def __init__(self):
        self.code = None
        self.headers = {}

    def send_response(self, code):
        self.code = code

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        pass


class TokenEnv:
    """Set WEBCAM_API_TOKEN for one test and pin SETTINGS_FILE away so the
    settings fallback can't leak a token from a developer's machine."""

    def __init__(self, token=None):
        self.token = token

    def __enter__(self):
        self._orig_token = os.environ.get("WEBCAM_API_TOKEN")
        self._orig_settings = api_server.SETTINGS_FILE
        api_server.SETTINGS_FILE = os.path.join(
            tempfile.gettempdir(), "webcam_no_settings_%d.json" % os.getpid())
        if self.token is None:
            os.environ.pop("WEBCAM_API_TOKEN", None)
        else:
            os.environ["WEBCAM_API_TOKEN"] = self.token
        return self

    def __exit__(self, *exc):
        if self._orig_token is None:
            os.environ.pop("WEBCAM_API_TOKEN", None)
        else:
            os.environ["WEBCAM_API_TOKEN"] = self._orig_token
        api_server.SETTINGS_FILE = self._orig_settings
        return False


class TestAuth(unittest.TestCase):
    def setUp(self):
        self.h = FakeHandler()

    def test_mutation_rejected_without_token_when_configured(self):
        with TokenEnv("s3cret"):
            code, body = self.h.run(
                "POST", "/api/delete?camera=front",
                json.dumps({"filename": "front_0.jpg"}).encode())
        self.assertEqual(code, 401)
        self.assertIn("unauthorized", body.get("error", ""))

    def test_mutation_allowed_with_bearer(self):
        with TokenEnv("s3cret"):
            code, _ = self.h.run(
                "POST", "/api/does-not-exist", b"{}",
                headers={"Authorization": "Bearer s3cret"})
        self.assertNotEqual(code, 401)

    def test_mutation_allowed_with_basic_password(self):
        cred = base64.b64encode(b"webcam:s3cret").decode()
        with TokenEnv("s3cret"):
            code, _ = self.h.run(
                "POST", "/api/does-not-exist", b"{}",
                headers={"Authorization": "Basic " + cred})
        self.assertNotEqual(code, 401)

    def test_mutation_allowed_with_basic_username(self):
        cred = base64.b64encode(b"s3cret:").decode()
        with TokenEnv("s3cret"):
            code, _ = self.h.run(
                "POST", "/api/does-not-exist", b"{}",
                headers={"Authorization": "Basic " + cred})
        self.assertNotEqual(code, 401)

    def test_wrong_token_rejected(self):
        with TokenEnv("s3cret"):
            code, _ = self.h.run(
                "POST", "/api/does-not-exist", b"{}",
                headers={"Authorization": "Bearer nope"})
        self.assertEqual(code, 401)

    def test_no_token_configured_allows_mutation(self):
        with TokenEnv(None):
            code, _ = self.h.run("POST", "/api/does-not-exist", b"{}")
        self.assertNotEqual(code, 401)


class TestCors(unittest.TestCase):
    def _send_with_origin(self, origin):
        rec = HeaderRecorder()
        h = api_server.Handler.__new__(api_server.Handler)
        h.send_response = rec.send_response
        h.send_header = rec.send_header
        h.end_headers = rec.end_headers
        h.headers = {"Origin": origin} if origin else {}
        h.wfile = io.BytesIO()
        h._send(200, {"ok": True})
        return rec

    def test_allowlisted_origin_echoed(self):
        os.environ["WEBCAM_CORS_ORIGIN"] = "http://gallery.test"
        try:
            rec = self._send_with_origin("http://gallery.test")
        finally:
            os.environ.pop("WEBCAM_CORS_ORIGIN", None)
        self.assertEqual(rec.headers.get("Access-Control-Allow-Origin"),
                         "http://gallery.test")
        self.assertEqual(rec.headers.get("Access-Control-Allow-Credentials"),
                         "true")

    def test_unknown_origin_gets_no_grant(self):
        os.environ["WEBCAM_CORS_ORIGIN"] = "http://gallery.test"
        try:
            rec = self._send_with_origin("http://evil.test")
        finally:
            os.environ.pop("WEBCAM_CORS_ORIGIN", None)
        self.assertNotIn("Access-Control-Allow-Origin", rec.headers)

    def test_wildcard_never_sent(self):
        rec = self._send_with_origin("")
        self.assertNotEqual(rec.headers.get("Access-Control-Allow-Origin"), "*")

    def test_wildcard_env_is_dropped(self):
        os.environ["WEBCAM_CORS_ORIGIN"] = "*"
        try:
            self.assertEqual(api_server.cors_origins(), [])
            rec = self._send_with_origin("http://anything.test")
        finally:
            os.environ.pop("WEBCAM_CORS_ORIGIN", None)
        self.assertNotIn("Access-Control-Allow-Origin", rec.headers)


class TestSseCap(unittest.TestCase):
    def _drain(self):
        while api_server.sse_active():
            api_server.sse_release()

    def test_cap_refuses_past_limit(self):
        os.environ["WEBCAM_SSE_MAX_CLIENTS"] = "2"
        try:
            self._drain()
            self.assertTrue(api_server.sse_try_acquire())
            self.assertTrue(api_server.sse_try_acquire())
            self.assertFalse(api_server.sse_try_acquire())
            self.assertEqual(api_server.sse_active(), 2)
            api_server.sse_release()
            self.assertTrue(api_server.sse_try_acquire())
            self.assertEqual(api_server.sse_active(), 2)
        finally:
            os.environ.pop("WEBCAM_SSE_MAX_CLIENTS", None)
            self._drain()

    def test_zero_disables_cap(self):
        os.environ["WEBCAM_SSE_MAX_CLIENTS"] = "0"
        try:
            self._drain()
            for _ in range(12):
                self.assertTrue(api_server.sse_try_acquire())
            self.assertEqual(api_server.sse_active(), 12)
        finally:
            os.environ.pop("WEBCAM_SSE_MAX_CLIENTS", None)
            self._drain()

    def test_stream_events_returns_503_when_full(self):
        h = FakeHandler()
        os.environ["WEBCAM_SSE_MAX_CLIENTS"] = "1"
        try:
            self._drain()
            self.assertTrue(api_server.sse_try_acquire())
            h.handler._send = h._send
            h.handler.headers = {}
            h.handler.wfile = io.BytesIO()
            h.handler.stream_events()
            self.assertEqual(h.sent[-1][0], 503)
        finally:
            os.environ.pop("WEBCAM_SSE_MAX_CLIENTS", None)
            self._drain()

    def test_slot_released_allows_reconnect(self):
        os.environ["WEBCAM_SSE_MAX_CLIENTS"] = "1"
        try:
            self._drain()
            self.assertTrue(api_server.sse_try_acquire())
            api_server.sse_release()
            self.assertTrue(api_server.sse_try_acquire())
        finally:
            os.environ.pop("WEBCAM_SSE_MAX_CLIENTS", None)
            self._drain()


if __name__ == "__main__":
    unittest.main()
