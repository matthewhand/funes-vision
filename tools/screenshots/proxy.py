#!/usr/bin/env python3
"""Serve the SYNTHETIC screenshot gallery + a stub /api on one origin.

Default root is tools/screenshots/fixtures/gallery — never a live camera
directory. Production paths under /mnt/models/Webcam21 or Webcam22 are
refused. POSTs are no-ops. Real api_server.py is not contacted unless
SCREENSHOT_API=live is set explicitly (not used for published shots).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from http.server import SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn, TCPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEFAULT_ROOT = os.path.join(HERE, "fixtures", "gallery")
DEFAULT_API_DIR = os.path.join(HERE, "fixtures", "api")
FORBIDDEN_MARKERS = ("/mnt/models/webcam21", "/mnt/models/webcam22")

ROOT = os.path.realpath(os.environ.get("SCREENSHOT_ROOT", DEFAULT_ROOT))
API_MODE = os.environ.get("SCREENSHOT_API", "stub").strip().lower() or "stub"
LIVE_API = os.environ.get("SCREENSHOT_LIVE_API", "http://localhost:8190")
API_DIR = os.path.realpath(os.environ.get("SCREENSHOT_API_DIR", DEFAULT_API_DIR))
INDEX_OVERRIDE = os.environ.get("SCREENSHOT_INDEX") or os.path.join(REPO, "index.html")
STATIC_DIR = os.environ.get("SCREENSHOT_STATIC") or REPO
PORT = int(os.environ.get("SCREENSHOT_PORT", "8899"))
IMG_EXT = (".jpg", ".jpeg", ".gif", ".png", ".webp")


def _is_forbidden(path):
    p = os.path.realpath(path).lower()
    return any(p == m or p.startswith(m + "/") for m in FORBIDDEN_MARKERS)


if _is_forbidden(ROOT):
    sys.stderr.write(
        f"REFUSING SCREENSHOT_ROOT={ROOT}\n"
        "Screenshot captures must not read live camera directories.\n"
        "Use tools/screenshots/fixtures/gallery (the default).\n"
    )
    sys.exit(2)


def _json_bytes(path, fallback):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return json.dumps(fallback).encode()


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def log_message(self, *a):
        return

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(404)
            return
        self._send(200, body, ctype)

    def _stub_api(self):
        path = self.path.split("?", 1)[0]
        if self.command == "POST":
            ln = int(self.headers.get("Content-Length", 0) or 0)
            if ln:
                self.rfile.read(ln)
            # Never mutate production. Pretend success.
            return self._send(200, b'{"ok":true,"fixture":true}', "application/json")
        if path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"event: ping\ndata: {}\n\n")
            try:
                self.wfile.flush()
            except Exception:
                pass
            return
        mapping = {
            "/api/settings": (os.path.join(API_DIR, "settings.json"), {}),
            "/api/status": (os.path.join(API_DIR, "status.json"), {"ok": False}),
            "/api/inference_log": (os.path.join(API_DIR, "inference_log.json"), []),
            "/api/integrations": (os.path.join(API_DIR, "integrations.json"), {"slack": {"enabled": False}}),
            "/api/pins": (os.path.join(ROOT, "pins.json"), []),
        }
        if path in mapping:
            fp, fb = mapping[path]
            return self._send(200, _json_bytes(fp, fb), "application/json")
        if path == "/api/llm-schema":
            schema = os.path.join(API_DIR, "llm-schema.json")
            if os.path.isfile(schema):
                return self._send_file(schema, "application/json")
            self.send_error(404)
            return
        if path == "/api/health":
            return self._send(200, b'{"status":"ok","fixture":true}', "application/json")
        self.send_error(404)

    def _proxy_live(self):
        if self.path.startswith("/api/events"):
            self.send_error(404)
            return
        try:
            data = None
            if self.command == "POST":
                ln = int(self.headers.get("Content-Length", 0) or 0)
                data = self.rfile.read(ln) if ln else b""
            req = urllib.request.Request(LIVE_API + self.path, data=data, method=self.command)
            for h in ("Content-Type", "Accept"):
                if self.headers.get(h):
                    req.add_header(h, self.headers[h])
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read()
                self.send_response(r.status)
                self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            try:
                self.wfile.write(str(e).encode())
            except Exception:
                pass

    def _serve_overlay(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html") and INDEX_OVERRIDE and os.path.isfile(INDEX_OVERRIDE):
            return self._send_file(INDEX_OVERRIDE, "text/html; charset=utf-8")
        # Static chrome from the repo if the fixture tree does not have it.
        rel = path.lstrip("/")
        if rel in ("manifest.json", "icon.svg", "favicon.ico", "lucide.min.js"):
            candidate = os.path.join(ROOT, rel)
            if not os.path.isfile(candidate):
                candidate = os.path.join(STATIC_DIR, rel)
            if os.path.isfile(candidate):
                ctype = {
                    "manifest.json": "application/manifest+json",
                    "icon.svg": "image/svg+xml",
                    "favicon.ico": "image/x-icon",
                    "lucide.min.js": "application/javascript",
                }[rel]
                return self._send_file(candidate, ctype)
        return super().do_GET()

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._stub_api() if API_MODE != "live" else self._proxy_live()
        return self._serve_overlay()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._stub_api() if API_MODE != "live" else self._proxy_live()
        self.send_error(405)


class TS(ThreadingMixIn, TCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print(
        f"serving root={ROOT} api={API_MODE} index={INDEX_OVERRIDE} on :{PORT} "
        f"(frames=FIXTURE, production-roots=REFUSED)"
    )
    TS(("127.0.0.1", PORT), H).serve_forever()
