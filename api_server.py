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
import subprocess
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PINS_FILE = os.path.join(BASE_DIR, "pins.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

# Deployment-specific; configured in settings.json
try:
    WATCH_DIRS = json.load(open(SETTINGS_FILE)).get("watch_dirs", [])
except (OSError, ValueError):
    WATCH_DIRS = []
PORT = 8190

# Settings keys the UI may change, with their allowed values
MUTABLE_SETTINGS = {
    "fast_pass_engine": ("yolo", "haar"),
    "deep_backfill": (True, False),
}


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


def pipeline_status():
    """Read-only snapshot of how the pipeline is configured and doing."""
    try:
        settings = json.load(open(SETTINGS_FILE))
    except (OSError, ValueError):
        settings = {}

    status = {"watch_dirs": WATCH_DIRS, "settings": settings,
              "trigger": {"inotify_active": False, "idle_sweep_seconds": 60},
              "llm": {"model": settings.get("model_local"), "reachable": False,
                      "allow_cloud": settings.get("allow_cloud", False)}}

    try:
        status["trigger"]["inotify_active"] = subprocess.run(
            ["pgrep", "-x", "inotifywait"], capture_output=True).returncode == 0
    except OSError:
        pass

    try:
        url = settings.get("ollama_url", "http://localhost:11434") + "/api/version"
        with urllib.request.urlopen(url, timeout=3):
            status["llm"]["reachable"] = True
    except Exception:
        pass

    try:
        analysis = json.load(open(os.path.join(BASE_DIR, "analysis.json")))
    except (OSError, ValueError):
        analysis = {}
    files = set()
    for d in WATCH_DIRS:
        try:
            files.update(f for f in os.listdir(d)
                         if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')))
        except OSError:
            pass
    # In-flight LLM call, if any ({} when idle)
    inference = {}
    try:
        inference = json.load(open(os.path.join(BASE_DIR, "inference_status.json")))
    except (OSError, ValueError):
        pass
    if inference.get("started"):
        inference["running_for_s"] = round(time.time() - inference["started"], 1)
    status["inference"] = inference

    status["queue"] = {
        "images_on_disk": len(files),
        "unanalyzed": max(0, len(files - set(analysis))),
        "unverified_partials": sum(1 for v in analysis.values() if v.get("fast_pass") == "partial"),
        "awaiting_backfill": sum(1 for v in analysis.values() if v.get("fast_pass") == "negative"),
        "llm_verified": sum(1 for v in analysis.values() if "fast_pass" not in v),
    }
    return status


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
        elif self.path == "/api/settings":
            try:
                settings = json.load(open(SETTINGS_FILE))
            except (OSError, ValueError):
                settings = {}
            self._send(200, {k: settings.get(k) for k in MUTABLE_SETTINGS})
        elif self.path == "/api/status":
            self._send(200, pipeline_status())
        elif self.path == "/api/inference_log":
            try:
                log = json.load(open(os.path.join(BASE_DIR, "inference_log.json")))
            except (OSError, ValueError):
                log = []
            self._send(200, list(reversed(log[-50:])))  # newest first
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

        if self.path == "/api/settings":
            changed = {}
            for key, allowed in MUTABLE_SETTINGS.items():
                if key in payload:
                    if payload[key] not in allowed:
                        self._send(400, {"error": f"{key} must be one of {list(allowed)}"})
                        return
                    changed[key] = payload[key]
            if not changed:
                self._send(400, {"error": "no recognized settings in payload"})
                return
            try:
                settings = json.load(open(SETTINGS_FILE))
            except (OSError, ValueError):
                settings = {}
            settings.update(changed)
            with open(SETTINGS_FILE, "w") as f:
                json.dump(settings, f, indent=2)
            print(f"Settings updated: {changed}")
            self._send(200, {"ok": True, **changed})

        elif self.path == "/api/pin":
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
