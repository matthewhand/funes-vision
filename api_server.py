#!/usr/bin/env python3
"""Tiny write API for the webcam gallery: pin and delete images.

The gallery itself is served by read-only nginx containers, so this
service is the single write channel. It runs on the host as the same
user as the analysis pipeline.

Endpoints (all JSON):
  GET  /api/pins              -> ["file1.jpg", ...]
  POST /api/pin    {"filename": f, "pinned": true|false}
  POST /api/delete {"filename": f}
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WATCH_DIRS = ["/mnt/models/Webcam21", "/mnt/models/Webcam22"]
BASE_DIR = "/home/user/webcam"
PINS_FILE = os.path.join(BASE_DIR, "pins.json")
PORT = 8190


def load_pins():
    if os.path.exists(PINS_FILE):
        return set(json.load(open(PINS_FILE)))
    return set()


def save_pins(pins):
    data = json.dumps(sorted(pins), indent=2)
    with open(PINS_FILE, "w") as f:
        f.write(data)
    # Sync to web roots so the UI sees pins on next load
    for d in WATCH_DIRS:
        try:
            with open(os.path.join(d, "pins.json"), "w") as f:
                f.write(data)
        except OSError:
            pass


def find_image(filename):
    """Return the directory containing filename, or None. Rejects paths."""
    if filename != os.path.basename(filename) or filename.startswith("."):
        return None
    for d in WATCH_DIRS:
        if os.path.isfile(os.path.join(d, filename)):
            return d
    return None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        if self.path == "/api/pins":
            self._send(200, sorted(load_pins()))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            filename = payload.get("filename", "")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "bad request"})
            return

        if self.path == "/api/pin":
            if find_image(filename) is None:
                self._send(404, {"error": "image not found"})
                return
            pins = load_pins()
            if payload.get("pinned"):
                pins.add(filename)
            else:
                pins.discard(filename)
            save_pins(pins)
            self._send(200, {"ok": True, "pinned": filename in pins})

        elif self.path == "/api/delete":
            image_dir = find_image(filename)
            if image_dir is None:
                self._send(404, {"error": "image not found"})
                return
            for path in (os.path.join(image_dir, filename),
                         os.path.join(image_dir, "thumbs", filename)):
                try:
                    os.remove(path)
                except OSError:
                    pass
            pins = load_pins()
            if filename in pins:
                pins.discard(filename)
                save_pins(pins)
            print(f"Deleted {filename} from {image_dir}")
            self._send(200, {"ok": True})

        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass  # quiet; deletions are logged explicitly


if __name__ == "__main__":
    print(f"Webcam API listening on :{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
