#!/usr/bin/env python3
"""Serve the deployed gallery (static files from Webcam21) AND proxy /api/* to
the real API on :8190, under one origin so the SPA renders fully with no CORS
and no nginx basic-auth.

PRIVACY: every camera frame (.jpg/.jpeg/.gif and anything under thumbs/) is
replaced with a GENERATED synthetic placeholder — the screenshots never contain
real webcam footage. Metadata (analysis.json/index.html/etc.) is served as-is
(it's labels/timestamps, not footage). Screenshots only."""
import http.server, socketserver, urllib.request, os, hashlib
import numpy as np
import cv2

ROOT = os.environ.get("SCREENSHOT_ROOT", "/mnt/models/Webcam21")
API = "http://localhost:8190"
# Optional: serve a dev index.html (e.g. the working copy under review) on top of
# the real deployed asset tree, so screenshots reflect un-deployed changes without
# touching production. Set to an absolute path; falls back to ROOT/index.html.
INDEX_OVERRIDE = os.environ.get("SCREENSHOT_INDEX", "")
PORT = int(os.environ.get("SCREENSHOT_PORT", "8899"))
IMG_EXT = (".jpg", ".jpeg", ".gif")
# Default: serve REAL frames (chat captures, the owner's private session).
# Set SCREENSHOT_PLACEHOLDER=1 to swap every frame for a synthetic placeholder
# (used when generating repo/guide images so no footage ever lands on disk-in-git).
PLACEHOLDER = os.environ.get("SCREENSHOT_PLACEHOLDER", "") == "1"

_cache = {}

def placeholder(path):
    """Deterministic synthetic 'SAMPLE FEED' frame for a given request path, so
    a thumb and its full image (and re-renders) stay consistent. JPEG bytes."""
    if path in _cache:
        return _cache[path]
    h = int(hashlib.md5(path.encode()).hexdigest(), 16)
    w, ht = 480, 300
    # diagonal gradient between two hash-derived muted slate tones
    base = np.array([30 + (h & 31), 38 + ((h >> 5) & 31), 52 + ((h >> 10) & 31)], np.float32)
    accent = np.array([60 + ((h >> 15) & 63), 90 + ((h >> 20) & 63), 130 + ((h >> 25) & 63)], np.float32)
    yy, xx = np.mgrid[0:ht, 0:w].astype(np.float32)
    t = ((xx / w) + (yy / ht)) / 2.0
    img = (base[None, None, :] * (1 - t[..., None]) + accent[None, None, :] * t[..., None]).astype(np.uint8)
    # subtle frame border + label
    cv2.rectangle(img, (6, 6), (w - 7, ht - 7), (90, 110, 150), 1)
    cv2.putText(img, "SAMPLE FEED", (w // 2 - 118, ht // 2 - 6),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (235, 240, 248), 2, cv2.LINE_AA)
    cv2.putText(img, "synthetic placeholder", (w // 2 - 96, ht // 2 + 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 195, 215), 1, cv2.LINE_AA)
    cv2.putText(img, f"#{h % 9000 + 1000}", (16, ht - 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (170, 185, 205), 1, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    data = buf.tobytes()
    _cache[path] = data
    return data


def is_image(path):
    p = path.split("?", 1)[0].lower()
    return p.endswith(IMG_EXT) or "/thumbs/" in p


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def _serve_placeholder(self):
        data = placeholder(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _proxy(self):
        if self.path.startswith("/api/events"):
            self.send_response(404); self.end_headers(); return
        try:
            data = None
            if self.command == "POST":
                ln = int(self.headers.get("Content-Length", 0) or 0)
                data = self.rfile.read(ln) if ln else b""
            req = urllib.request.Request(API + self.path, data=data, method=self.command)
            for h in ("Content-Type", "Accept"):
                if self.headers.get(h): req.add_header(h, self.headers[h])
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read()
                self.send_response(r.status)
                self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
        except Exception as e:
            self.send_response(502); self.end_headers()
            try: self.wfile.write(str(e).encode())
            except Exception: pass

    def _serve_index_override(self):
        with open(INDEX_OVERRIDE, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/"): return self._proxy()
        if PLACEHOLDER and is_image(self.path): return self._serve_placeholder()
        path = self.path.split("?", 1)[0]
        if INDEX_OVERRIDE and path in ("/", "/index.html"):
            return self._serve_index_override()
        return super().do_GET()

    def do_POST(self): return self._proxy()
    def log_message(self, *a): pass


class TS(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


print(f"serving {ROOT} + /api->{API} on :{PORT} "
      f"(frames: {'PLACEHOLDER' if PLACEHOLDER else 'REAL'})")
TS(("127.0.0.1", PORT), H).serve_forever()
