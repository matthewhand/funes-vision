"""Screenshot proxy must never serve live camera roots."""
import http.client
import importlib.util
import os
import socket
import subprocess
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY = os.path.join(HERE, "tools", "screenshots", "proxy.py")


def load_proxy():
    spec = importlib.util.spec_from_file_location("webcam_shot_proxy", PROXY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _serve(mod):
    srv = mod.TS(("127.0.0.1", 0), mod.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _stop(srv):
    srv.shutdown()
    srv.server_close()


def _sse_connect(host, port):
    sock = socket.create_connection((host, port), timeout=2)
    sock.sendall(
        b"GET /api/events HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Accept: text/event-stream\r\n"
        b"Connection: close\r\n\r\n"
    )
    return sock


def _recv_until(sock, pred, timeout):
    sock.settimeout(0.05)
    buf = b""
    closed = False
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                closed = True
                break
            buf += chunk
            if pred(buf):
                break
        except socket.timeout:
            continue
    return buf, closed


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

    def test_refuses_live_roots_at_startup(self):
        env = os.environ.copy()
        for root in ("/mnt/models/Webcam21", "/mnt/models/Webcam22"):
            env["SCREENSHOT_ROOT"] = root
            r = subprocess.run(
                [sys.executable, PROXY],
                env=env,
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(r.returncode, 2, r.stderr.decode())
            self.assertIn(b"REFUSING", r.stderr)

    def test_stub_sse_first_ping_then_cap(self):
        p = load_proxy()
        self.assertEqual(p.API_MODE, "stub")
        p.SSE_STUB_INTERVAL = 2.0
        p.SSE_STUB_MAX_SEC = 0.25
        srv = _serve(p)
        try:
            host, port = srv.server_address
            sock = _sse_connect(host, port)
            t0 = time.monotonic()
            buf, _ = _recv_until(sock, lambda b: b"event: ping" in b, 0.3)
            self.assertIn(b"text/event-stream", buf)
            self.assertIn(b"event: ping", buf)
            self.assertLess(time.monotonic() - t0, 0.3)
            _, closed = _recv_until(sock, lambda b: False, 1.2)
            sock.close()
            self.assertTrue(closed)
            self.assertLess(time.monotonic() - t0, 1.0)
        finally:
            _stop(srv)

    def test_stub_sse_repeats_pings(self):
        p = load_proxy()
        p.SSE_STUB_INTERVAL = 0.06
        p.SSE_STUB_MAX_SEC = 0.28
        srv = _serve(p)
        try:
            host, port = srv.server_address
            sock = _sse_connect(host, port)
            buf, closed = _recv_until(sock, lambda b: False, 1.2)
            sock.close()
            self.assertGreaterEqual(buf.count(b"event: ping"), 3)
            self.assertTrue(closed)
        finally:
            _stop(srv)

    def test_stub_sse_client_disconnect(self):
        p = load_proxy()
        p.SSE_STUB_INTERVAL = 0.05
        p.SSE_STUB_MAX_SEC = 30
        srv = _serve(p)
        try:
            host, port = srv.server_address
            sock = _sse_connect(host, port)
            buf, _ = _recv_until(sock, lambda b: b"event: ping" in b, 0.4)
            self.assertIn(b"event: ping", buf)
            sock.close()
            conn = http.client.HTTPConnection(host, port, timeout=2)
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            self.assertIn(b"fixture", resp.read())
            conn.close()
        finally:
            _stop(srv)

    def test_live_api_404s_events(self):
        p = load_proxy()
        p.API_MODE = "live"
        srv = _serve(p)
        try:
            host, port = srv.server_address
            conn = http.client.HTTPConnection(host, port, timeout=2)
            conn.request("GET", "/api/events")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 404)
            resp.read()
            conn.close()
        finally:
            _stop(srv)

    def test_serves_user_guide_from_docs(self):
        p = load_proxy()
        srv = _serve(p)
        try:
            host, port = srv.server_address
            conn = http.client.HTTPConnection(host, port, timeout=2)
            conn.request("GET", "/USER-GUIDE.html")
            resp = conn.getresponse()
            body = resp.read()
            self.assertEqual(resp.status, 200)
            self.assertIn(b"text/html", resp.getheader("Content-Type", "").encode())
            self.assertIn(b"Webcam gallery", body)
            self.assertNotIn(b"/mnt/models/Webcam21", body)
            conn.close()
            conn = http.client.HTTPConnection(host, port, timeout=2)
            conn.request("GET", "/guide/img/timeline.png")
            resp = conn.getresponse()
            img = resp.read()
            self.assertEqual(resp.status, 200)
            self.assertGreater(len(img), 1000)
            self.assertTrue(img.startswith(b"\x89PNG") or img[:3] == b"\xff\xd8\xff")
            conn.close()
        finally:
            _stop(srv)


if __name__ == "__main__":
    unittest.main()
