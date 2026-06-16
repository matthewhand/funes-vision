#!/usr/bin/env python3
"""Tiny write API for the webcam gallery: pin and delete images.

The gallery itself is served by read-only nginx containers, so this
service is the single write channel. It runs on the host as the same
user as the analysis pipeline.

Endpoints (JSON unless noted):
  GET  /api/pins                  -> ["file1.jpg", ...]
  POST /api/pin    {"filename": f, "pinned": true|false}
  POST /api/delete {"filename": f}
  GET/POST /api/settings          -> MUTABLE_SETTINGS only (validated)
  GET/POST /api/integrations      -> redacted integration config
  POST /api/integrations/test     -> send a Slack test message
  GET  /api/status                -> pipeline/camera/disk/metrics snapshot
  GET  /api/health                -> {ok|degraded} for uptime monitors (200/503)
  GET  /api/inference_log         -> recent LLM audit trail
  GET  /api/events                -> SSE stream: new-detection / new-burst
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
# Integration config + secrets (Slack tokens etc). Deliberately separate
# from settings.json: gitignored, mode 600, and NOT synced into the public
# nginx web roots, so bot tokens never become world-readable.
INTEGRATIONS_FILE = os.path.join(BASE_DIR, "integrations.json")

# Deployment-specific; configured in settings.json
try:
    WATCH_DIRS = json.load(open(SETTINGS_FILE)).get("watch_dirs", [])
except (OSError, ValueError):
    WATCH_DIRS = []
PORT = 8190

# Settings keys the UI may change: either enumerated choices or a
# numeric range
MUTABLE_SETTINGS = {
    "fast_pass_engine": {"choices": ("yolo", "haar")},
    "deep_backfill": {"choices": (True, False)},
    "deep_passes_enabled": {"choices": (True, False)},
    "idle_sweep_seconds": {"min": 15, "max": 3600},
}

def setting_valid(key, value):
    spec = MUTABLE_SETTINGS[key]
    if "choices" in spec:
        return value in spec["choices"]
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and spec["min"] <= value <= spec["max"])


def load_integrations():
    try:
        with open(INTEGRATIONS_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _integration_state():
    try:
        return json.load(open(os.path.join(BASE_DIR, "integrations_state.json")))
    except (OSError, ValueError):
        return {}


def redacted_integrations():
    """Public-safe view: presence of tokens, never their values."""
    slack = load_integrations().get("slack") or {}
    return {
        "slack": {
            "enabled": bool(slack.get("enabled")),
            "has_bot_token": bool(slack.get("bot_token")),
            "has_app_token": bool(slack.get("app_token")),
            "channel_id": slack.get("channel_id", ""),
            "public_base_url": slack.get("public_base_url", ""),
            "notify_mode": slack.get("notify_mode", "context"),
            "last_delivery": (_integration_state().get("slack") or {}).get("last_delivery"),
        }
    }


def save_slack_settings(changes):
    """Merge ``changes`` into the slack block, preserving omitted fields."""
    data = load_integrations()
    slack = data.get("slack") or {}
    slack.update(changes)
    data["slack"] = slack
    fd = os.open(INTEGRATIONS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)


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


# Pipeline liveness: create-index.sh touches this every sweep (incl. the ~60s
# idle loop), so a recent mtime means the pipeline is alive even when cameras
# are quiet. Cameras only capture on motion, so "stale" is generous.
LASTRUN_MARKER = "/tmp/webcam_analysis.lastrun"
CAMERA_STALE_S = 6 * 3600
SWEEP_STALE_S = 3600  # no sweep in 1h (= analysis lock timeout) -> stalled.
                      # A full both-camera catch-up sweep legitimately takes
                      # ~45 min, so a shorter window false-degrades /api/health.
IMG_EXTS = ('.jpg', '.jpeg', '.png', '.gif')


def _inference_metrics(window_s=3600):
    """Roll up inference_log.json over the last window into health numbers."""
    try:
        log = json.load(open(os.path.join(BASE_DIR, "inference_log.json")))
    except (OSError, ValueError):
        log = []
    now = time.time()
    recent = [e for e in log if now - e.get("started", 0) <= window_s]
    if not recent:
        return {"window_min": window_s // 60, "count": 0}
    ok = sum(1 for e in recent if e.get("ok"))
    durs = sorted(e.get("duration_s", 0) for e in recent)
    # OpenRouter model ids contain a '/'; local Ollama tags don't
    cloud = sum(1 for e in recent if "/" in (e.get("model") or ""))
    return {
        "window_min": window_s // 60,
        "count": len(recent),
        "ok": ok,
        "failures": len(recent) - ok,
        "success_rate": round(ok / len(recent), 3),
        "local": len(recent) - cloud,
        "cloud": cloud,
        "avg_s": round(sum(durs) / len(durs), 1),
        "p95_s": round(durs[min(len(durs) - 1, int(len(durs) * 0.95))], 1),
    }


def _fs_stats():
    """Free space on the filesystem hosting the images (shared with other
    services, so worth watching independently of the per-camera budgets)."""
    try:
        target = WATCH_DIRS[0] if WATCH_DIRS else BASE_DIR
        st = os.statvfs(target)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        return {"free_gb": round(free / 1024 ** 3, 1),
                "total_gb": round(total / 1024 ** 3, 1),
                "used_pct": round(100 * (total - free) / total, 1) if total else None}
    except OSError:
        return None


def _camera_stats(max_dir_gb):
    """Per-camera liveness + disk usage (one scandir per dir)."""
    now = time.time()
    budget = max_dir_gb * 1024 ** 3
    cams = []
    for d in WATCH_DIRS:
        newest, count, total = 0.0, 0, 0
        try:
            with os.scandir(d) as it:
                for e in it:
                    if not (e.is_file() and e.name.lower().endswith(IMG_EXTS)):
                        continue
                    try:
                        st = e.stat()
                    except OSError:
                        continue
                    count += 1
                    total += st.st_size
                    newest = max(newest, st.st_mtime)
        except OSError:
            pass
        cams.append({
            "name": os.path.basename(d.rstrip("/")),
            "images": count,
            "bytes": total,
            "budget_pct": round(100 * total / budget, 1) if budget else None,
            "last_frame_age_s": round(now - newest) if newest else None,
            "stale": (newest == 0) or (now - newest > CAMERA_STALE_S),
        })
    return cams


def pipeline_status():
    """Read-only snapshot of how the pipeline is configured and doing."""
    try:
        settings = json.load(open(SETTINGS_FILE))
    except (OSError, ValueError):
        settings = {}

    status = {"watch_dirs": WATCH_DIRS, "settings": settings,
              "trigger": {"inotify_active": False,
                          "idle_sweep_seconds": settings.get("idle_sweep_seconds", 60)},
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
        age = round(time.time() - inference["started"], 1)
        # A real call can't outlive ~2x its 900s timeout; an older marker means
        # the process died without clearing it -> report idle, not a phantom
        # "Analyzing for 45m" in the UI.
        if age > 1800:
            inference = {}
        else:
            inference["running_for_s"] = age
    status["inference"] = inference

    status["queue"] = {
        "images_on_disk": len(files),
        "unanalyzed": max(0, len(files - set(analysis))),
        "unverified_partials": sum(1 for v in analysis.values() if v.get("fast_pass") == "partial"),
        "awaiting_backfill": sum(1 for v in analysis.values() if v.get("fast_pass") == "negative"),
        "llm_verified": sum(1 for v in analysis.values() if "fast_pass" not in v),
    }

    # Liveness + observability
    try:
        status["trigger"]["last_sweep_age_s"] = round(time.time() - os.path.getmtime(LASTRUN_MARKER))
    except OSError:
        status["trigger"]["last_sweep_age_s"] = None
    status["cameras"] = _camera_stats(settings.get("max_dir_gb", 4.0))
    status["filesystem"] = _fs_stats()
    status["metrics"] = _inference_metrics()
    try:
        rlog = json.load(open(os.path.join(BASE_DIR, "retention_log.json")))
        status["retention"] = rlog[-1] if rlog else None
    except (OSError, ValueError):
        status["retention"] = None
    return status


def health_summary():
    """Compact health for an external uptime monitor: ok | degraded."""
    s = pipeline_status()
    swept = s["trigger"].get("last_sweep_age_s")
    fs = s.get("filesystem")
    llm_on = s.get("settings", {}).get("deep_passes_enabled", True)
    checks = {
        "inotify": bool(s["trigger"]["inotify_active"]),
        # When deep passes are intentionally disabled, an unreachable LLM is
        # expected, not degraded.
        "llm_reachable": (not llm_on) or bool(s["llm"]["reachable"]),
        "recent_sweep": swept is not None and swept < SWEEP_STALE_S,
        "disk_space": bool(fs and fs.get("free_gb", 0) > 1.0),
    }
    return {
        "status": "ok" if all(checks.values()) else "degraded",
        "checks": checks,
        "cameras": [{"name": c["name"], "last_frame_age_s": c["last_frame_age_s"], "stale": c["stale"]}
                    for c in s.get("cameras", [])],
    }


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
        elif self.path == "/api/integrations":
            self._send(200, redacted_integrations())
        elif self.path == "/api/status":
            self._send(200, pipeline_status())
        elif self.path == "/api/health":
            h = health_summary()
            self._send(200 if h["status"] == "ok" else 503, h)
        elif self.path == "/api/inference_log":
            try:
                log = json.load(open(os.path.join(BASE_DIR, "inference_log.json")))
            except (OSError, ValueError):
                log = []
            self._send(200, list(reversed(log[-50:])))  # newest first
        elif self.path == "/api/events":
            self.stream_events()
        else:
            self._send(404, {"error": "not found"})

    # --- Server-Sent Events: push new detections / bursts to the UI ---
    @staticmethod
    def _verified_detections(analysis):
        """Files carrying a final LLM verdict with at least one true label."""
        out = {}
        for f, v in analysis.items():
            if "fast_pass" in v:
                continue  # detector-only, not yet a verdict
            labels = sorted(k for k, val in v.items() if val is True and k != "_yolo")
            if labels:
                out[f] = labels
        return out

    def _sse(self, event, data):
        self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def stream_events(self):
        """Long-lived text/event-stream emitting new-detection / new-burst as
        the pipeline writes analysis.json / bursts.json. Cheap: it polls file
        mtimes and only re-reads on change. The first pass seeds state without
        emitting, so a client doesn't get blasted with the whole backlog."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")  # ask any proxy not to buffer
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        analysis_path = os.path.join(BASE_DIR, "analysis.json")
        bursts_path = os.path.join(BASE_DIR, "bursts.json")
        seen_det, seen_bursts = {}, set()
        a_mtime = b_mtime = -1.0
        seeded = False
        try:
            while True:
                try:
                    amt = os.path.getmtime(analysis_path)
                except OSError:
                    amt = 0.0
                if amt != a_mtime:
                    a_mtime = amt
                    try:
                        cur = self._verified_detections(json.load(open(analysis_path)))
                    except (OSError, ValueError):
                        cur = {}
                    if seeded:
                        for f in cur.keys() - seen_det.keys():
                            self._sse("new-detection", {"file": f, "labels": cur[f]})
                    seen_det = cur

                try:
                    bmt = os.path.getmtime(bursts_path)
                except OSError:
                    bmt = 0.0
                if bmt != b_mtime:
                    b_mtime = bmt
                    try:
                        bursts = json.load(open(bursts_path))
                    except (OSError, ValueError):
                        bursts = {}
                    if seeded:
                        for bid in bursts.keys() - seen_bursts:
                            self._sse("new-burst", {"id": bid,
                                                    "summary": bursts.get(bid, {}).get("summary", "")})
                    seen_bursts = set(bursts)

                seeded = True
                self.wfile.write(b": ping\n\n")  # heartbeat keeps the connection alive
                self.wfile.flush()
                time.sleep(3)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return  # client went away

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
            for key, spec in MUTABLE_SETTINGS.items():
                if key in payload:
                    if not setting_valid(key, payload[key]):
                        detail = list(spec["choices"]) if "choices" in spec else f"{spec['min']}..{spec['max']}"
                        self._send(400, {"error": f"{key} must be {detail}"})
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

        elif self.path == "/api/integrations":
            slack_in = payload.get("slack") or {}
            changes = {}
            if "enabled" in slack_in:
                changes["enabled"] = bool(slack_in["enabled"])
            # Blank token fields preserve the stored secret (the redacted
            # GET means the UI can't echo it back to re-submit)
            for tok in ("bot_token", "app_token"):
                v = slack_in.get(tok)
                if isinstance(v, str) and v.strip():
                    changes[tok] = v.strip()
            if "channel_id" in slack_in:
                changes["channel_id"] = str(slack_in["channel_id"]).strip()
            if "public_base_url" in slack_in:
                url = str(slack_in["public_base_url"]).strip()
                if url and not url.startswith(("http://", "https://")):
                    self._send(400, {"error": "public_base_url must start with http:// or https://"})
                    return
                changes["public_base_url"] = url
            if "notify_mode" in slack_in:
                mode = str(slack_in["notify_mode"]).strip()
                if mode not in ("context", "objects", "all"):
                    self._send(400, {"error": "notify_mode must be context, objects, or all"})
                    return
                changes["notify_mode"] = mode
            if not changes:
                self._send(400, {"error": "no integration settings in payload"})
                return
            save_slack_settings(changes)
            print(f"Integrations updated: slack {sorted(changes)}")
            self._send(200, {"ok": True, **redacted_integrations()})

        elif self.path == "/api/integrations/test":
            slack = load_integrations().get("slack") or {}
            try:
                from integrations import slack as slack_mod
                ok, detail = slack_mod.send_test_message(slack)
            except Exception as e:
                ok, detail = False, str(e)
            self._send(200 if ok else 400, {"ok": ok, "detail": detail})

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
