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
  GET  /api/llm-schema            -> live prompt + front/back HA JSON schemas
  POST /api/clip                  -> GIF (player) or MP4 (API-only) from frame names
  GET  /api/events                -> SSE stream: image.new / new-detection / detection.preliminary / new-burst
"""
import json
import os
import re
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

# --- Camera registry -------------------------------------------------------
# Single source of truth for camera dirs / labels / ids. Derived from
# `watch_dirs` so there is no second config to drift: the pipeline, the API,
# and the SPA all read the same list. Backward compatible — the registry is
# built from what is already configured, and every `?camera=` param below
# falls back to the first camera when absent (existing callers keep working).
def cameras():
    """[{id, label, source_dir}] for every configured watch dir, in order.

    The id is positional — the first watch dir is `front`, every other is
    `back`. Deriving it from the path string (camera_kind) is not safe: any dir
    that is not literally Webcam21/Webcam22 or 10.0.0.21/22 collapses to
    `front`, so two cameras would share one id and every `?camera=` call
    would be ambiguous. Positional ids are also what
    settings.json's `ignore_regions[].camera` keys already use.
    """
    out = []
    for i, d in enumerate(WATCH_DIRS):
        out.append({
            "id": "front" if i == 0 else "back",
            "label": "Front" if i == 0 else "Back",
            "source_dir": d,
            "index": i,
        })
    return out


def camera_id_for_dir(directory):
    """id of the camera whose source_dir == directory, else None."""
    for c in cameras():
        if c["source_dir"] == directory:
            return c["id"]
    return None


def default_camera_id():
    """Kept for callers that still assume a single camera. Prefer
    `resolve_camera(query)` — it returns None for "all cameras" rather than
    silently picking one, which is what made the old single-camera default
    leak one camera's data into the other's view."""
    cs = cameras()
    return cs[0]["id"] if cs else None


def resolve_camera(query):
    """`?camera=<id>` -> that camera's id, or None for "all cameras".

    An unknown id is a 400, not a silent fallback: a client that thinks it is
    talking to camera X must not silently get camera Y's data. Absent means
    "all cameras" — the read endpoints used to return everything anyway, so
    this is backward compatible. The write endpoints (pin/delete) require an
    explicit id, because writing to all cameras at once is not a thing.
    """
    cs = cameras()
    if not cs:
        return None
    if query in (None, "", "all"):
        return None
    for c in cs:
        if c["id"] == query:
            return c["id"]
    raise ValueError(f"unknown camera {query!r}; known: {[c['id'] for c in cs]}")


def camera_dir(camera_id):
    for c in cameras():
        if c["id"] == camera_id:
            return c["source_dir"]
    return None

# Settings keys the UI may change: either enumerated choices or a
# numeric range
MUTABLE_SETTINGS = {
    "fast_pass_engine": {"choices": ("yolo", "haar")},
    "deep_backfill": {"choices": (True, False)},
    "deep_passes_enabled": {"choices": (True, False)},
    "burst_summaries_enabled": {"choices": (True, False)},
    "idle_sweep_seconds": {"min": 15, "max": 3600},
    "ignore_regions": {"kind": "ignore_regions"},
}

def resolve_timezone(env_val=None, settings_val=None):
    """Display timezone resolution: WEBCAM_TZ env > settings.json `timezone`
    > "Australia/Sydney". Blank/whitespace values are ignored."""
    for v in (env_val, settings_val):
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "Australia/Sydney"


def setting_valid(key, value):
    spec = MUTABLE_SETTINGS[key]
    if spec.get("kind") == "ignore_regions":
        import zones
        return zones.ignore_regions_valid(value)
    if "choices" in spec:
        return value in spec["choices"]
    return (isinstance(value, int) and not isinstance(value, bool)
            and spec["min"] <= value <= spec["max"])


class IntegrationsUnreadable(Exception):
    """Existing integrations.json cannot be merged; writers must not truncate it."""


def _read_integrations_obj():
    """Parse INTEGRATIONS_FILE as a JSON object.

    Zero-length file → {}. Missing → FileNotFoundError. Unreadable → OSError.
    Non-zero content that is not a JSON object → ValueError (do not overwrite).
    """
    with open(INTEGRATIONS_FILE) as f:
        raw = f.read()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise ValueError("integrations.json is unparseable") from e
    if not isinstance(data, dict):
        raise ValueError("integrations.json is not an object")
    return data


def load_integrations():
    """Best-effort read. Missing/corrupt → {} (fail closed)."""
    try:
        return _read_integrations_obj()
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


def integrations_get_response():
    """(status, body) for GET /api/integrations.

    Missing or empty file → 200 + redacted empty Slack view.
    Present, non-empty, and unparseable/unreadable → 409 (do not invent Slack).
    """
    try:
        _read_integrations_obj()
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        return 409, {
            "ok": False,
            "detail": "integrations.json is unreadable",
            "unreadable": True,
        }
    return 200, redacted_integrations()


def save_slack_settings(changes):
    """Merge ``changes`` into the slack block, preserving omitted fields.

    Refuses to write if the existing file is present but unreadable or
    corrupt, so MQTT/ntfy secrets are never truncated away.
    """
    try:
        data = _read_integrations_obj()
    except FileNotFoundError:
        data = {}
    except OSError as e:
        raise IntegrationsUnreadable(
            f"integrations.json is unreadable; refusing to overwrite ({e})"
        ) from e
    except ValueError as e:
        raise IntegrationsUnreadable(f"{e}; refusing to overwrite") from e
    slack = data.get("slack") if isinstance(data.get("slack"), dict) else {}
    slack.update(changes)
    data["slack"] = slack
    from analyze_images import _atomic_write_json
    _atomic_write_json(INTEGRATIONS_FILE, data, indent=2)
    os.chmod(INTEGRATIONS_FILE, 0o600)


def _pins_path(camera_id):
    """Per-camera pins file. One camera's pins never leak into another's
    web root — the old single PINS_FILE was synced to every root, so a pin on
    camera A showed up in camera B's gallery too."""
    d = camera_dir(camera_id)
    if not d:
        return PINS_FILE
    return os.path.join(d, "pins.json")


def load_pins(camera_id=None):
    """Pins for one camera, or the union of every camera's pins.

    `camera_id=None` (the default, and what `/api/pins` with no `?camera=`
    sends) returns every camera's pins merged — the old single-file behaviour
    for callers that don't care which camera. An explicit id returns only
    that camera's pins, which is what the per-camera UI needs.
    """
    if camera_id is None:
        out = set()
        for c in cameras():
            p = _pins_path(c["id"])
            if os.path.exists(p):
                try:
                    out.update(json.load(open(p)))
                except (OSError, ValueError):
                    pass
        if not out and os.path.exists(PINS_FILE):
            try:
                return set(json.load(open(PINS_FILE)))
            except (OSError, ValueError):
                pass
        return out
    path = _pins_path(camera_id)
    if os.path.exists(path):
        try:
            return set(json.load(open(path)))
        except (OSError, ValueError):
            return set()
    # Legacy: fall back to the repo-wide pins file (pre-camera-scoping).
    if os.path.exists(PINS_FILE):
        try:
            return set(json.load(open(PINS_FILE)))
        except (OSError, ValueError):
            return set()
    return set()


def save_pins(pins, camera_id):
    """Write one camera's pins. `camera_id` is mandatory — saving "all cameras"
    as one set is exactly the leak this module exists to prevent."""
    data = json.dumps(sorted(pins), indent=2)
    path = _pins_path(camera_id)
    with open(path, "w") as f:
        f.write(data)
    os.chmod(path, 0o644)   # nginx serves these read-only as www-data


def find_image(filename, camera_id=None):
    """Return the directory containing filename, or None. Rejects paths.

    With `camera_id` the search is restricted to that camera's dir — a file
    that exists in more than one camera is ambiguous until you say which
    camera you mean, and pin/delete are per-camera operations. Without it
    the search covers every watch dir, which is what the clip path and the
    SSE bridge need (they don't care which camera a frame came from).
    """
    if filename != os.path.basename(filename) or filename.startswith("."):
        return None
    if camera_id:
        d = camera_dir(camera_id)
        if d and os.path.isfile(os.path.join(d, filename)):
            return d
        return None
    for d in WATCH_DIRS:
        if os.path.isfile(os.path.join(d, filename)):
            return d
    return None


# Pipeline liveness: create-index.sh touches this every sweep (incl. the ~60s
# idle loop), so a recent mtime means the pipeline is alive even when cameras
# are quiet. Cameras only capture on motion, so "stale" is generous.
LASTRUN_MARKER = "/tmp/webcam_analysis.lastrun"
SWEEP_STALE_S = 3600  # no sweep in 1h (= analysis lock timeout) -> stalled.
                      # A full both-camera catch-up sweep legitimately takes
                      # ~45 min, so a shorter window false-degrades /api/health.


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


def _camera_stale_s(settings=None):
    """Same silence clock as analyze_images.run_health_checks (default 24h)."""
    from analyze_images import camera_offline_hours
    return camera_offline_hours(settings) * 3600


def _camera_stats(max_dir_gb, settings=None):
    """Per-camera liveness + disk usage (images + matching thumbs)."""
    from analyze_images import dir_image_usage
    now = time.time()
    budget = max_dir_gb * 1024 ** 3
    stale_s = _camera_stale_s(settings)
    cams = []
    for d in WATCH_DIRS:
        newest, count, total = dir_image_usage(d)
        cams.append({
            "name": os.path.basename(d.rstrip("/")),
            "images": count,
            "bytes": total,
            "budget_pct": round(100 * total / budget, 1) if budget else None,
            "last_frame_age_s": round(now - newest) if newest else None,
            "stale": (newest == 0) or (now - newest > stale_s),
        })
    return cams



def queue_from_analysis(analysis):
    """Classify analysis.json records for /api/status queue counts.

    Uses catalog.kind so folklore shapes and schema-1 rows agree.
    """
    import catalog
    unverified = awaiting = verified = 0
    for v in analysis.values():
        k = catalog.kind(v)
        if k in ("preliminary", "skip"):
            unverified += 1
        if k in ("negative", "no_trigger"):
            awaiting += 1
        if k == "verified":
            verified += 1
    return {
        "unverified_partials": unverified,
        "awaiting_backfill": awaiting,
        "llm_verified": verified,
    }


def pipeline_status(camera_id=None):
    """Read-only snapshot of how the pipeline is configured and doing.

    `camera_id` scopes the per-camera fields (cameras list, images on disk,
    queue counts) to one camera. Global fields (settings, LLM, trigger,
    filesystem, timezone, inference metrics) are never scoped — they describe
    the box, not a camera. With no camera_id the status covers everything
    (backward compatible with existing callers).
    """
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

    # Per-camera fields: which dirs to look at for this request.
    try:
        analysis = json.load(open(os.path.join(BASE_DIR, "analysis.json")))
    except (OSError, ValueError):
        analysis = {}
    files = set()
    for d in ([camera_dir(camera_id)] if camera_id else WATCH_DIRS):
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
        **queue_from_analysis(analysis),
    }

    # Liveness + observability
    try:
        status["trigger"]["last_sweep_age_s"] = round(time.time() - os.path.getmtime(LASTRUN_MARKER))
    except OSError:
        status["trigger"]["last_sweep_age_s"] = None
    all_cameras = _camera_stats(settings.get("max_dir_gb", 5.0), settings)
    if camera_id:
        scoped = camera_dir(camera_id)
        status["cameras"] = [c for c in all_cameras
                             if scoped and c["name"] == os.path.basename(scoped.rstrip("/"))]
    else:
        status["cameras"] = all_cameras
    status["filesystem"] = _fs_stats()
    status["timezone"] = resolve_timezone(os.getenv("WEBCAM_TZ"), settings.get("timezone"))
    status["metrics"] = _inference_metrics()
    # Honest serial-LLM budget: priority rows × last-window average (or 40s).
    q = status["queue"]
    if settings.get("deep_passes_enabled", True) is False:
        q["deep_s_per_frame"] = None
        q["deep_eta_s"] = None
    else:
        avg = float((status["metrics"] or {}).get("avg_s") or 40)
        q["deep_s_per_frame"] = round(avg, 1)
        q["deep_eta_s"] = int(q.get("unverified_partials", 0) * avg)
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

    _BAD_CAMERA = object()

    def _camera_param(self):
        """`?camera=<id>` from the query string.

        Returns the camera id, `_BAD_CAMERA` on an unknown id (a client that
        thinks it is talking to camera X must not silently get camera Y's
        data), or None when the param is absent — meaning "all cameras" for
        the read endpoints. The write endpoints reject None themselves.
        """
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        raw = (q.get("camera") or [None])[0]
        try:
            return resolve_camera(raw)
        except ValueError as e:
            self._send(400, {"error": str(e)})
            return self._BAD_CAMERA

    @staticmethod
    def _load_catalog(camera_id):
        """One camera's four catalogs as a single payload.

        The SPA fetches these as relative URLs (images.json, analysis.json,
        bursts.json, pins.json), which nginx scopes by whichever web root served
        the page. A dashboard at `/` has no such root, so it cannot reach a
        camera's catalog without this endpoint. Returns {} for any missing file
        rather than erroring — a camera that has never been swept is a valid
        state, not a failure.
        """
        d = camera_dir(camera_id)
        if not d:
            return {}
        out = {}
        for key, name in (("images", "images.json"),
                          ("analysis", "analysis.json"),
                          ("bursts", "bursts.json"),
                          ("pins", "pins.json")):
            try:
                with open(os.path.join(d, name)) as f:
                    out[key] = json.load(f)
            except (OSError, ValueError):
                out[key] = [] if key in ("images", "pins") else {}
        return out

    def do_GET(self):
        # Parse `?camera=<id>` off the path first, then dispatch on the bare
        # path — otherwise every scoped request falls through to 404 because
        # "/api/pins?camera=back" != "/api/pins".
        camera_id = self._camera_param()
        self.path = self.path.split("?", 1)[0]
        if camera_id is self._BAD_CAMERA:
            return
        if self.path == "/api/pins":
            self._send(200, sorted(load_pins(camera_id)))
        elif self.path == "/api/settings":
            try:
                settings = json.load(open(SETTINGS_FILE))
            except (OSError, ValueError):
                settings = {}
            out = {k: settings.get(k) for k in MUTABLE_SETTINGS}
            out["cameras"] = cameras()
            self._send(200, out)
        elif self.path == "/api/integrations":
            code, body = integrations_get_response()
            self._send(code, body)
        elif self.path == "/api/status":
            self._send(200, pipeline_status(camera_id))
        elif self.path == "/api/health":
            h = health_summary()
            self._send(200 if h["status"] == "ok" else 503, h)
        elif self.path == "/api/inference_log":
            try:
                log = json.load(open(os.path.join(BASE_DIR, "inference_log.json")))
            except (OSError, ValueError):
                log = []
            self._send(200, list(reversed(log[-50:])))  # newest first
        elif self.path == "/api/llm-schema":
            # Serve the live LLM prompt and HA output schemas for UI display
            try:
                from analyze_images import get_llm_schema
                self._send(200, get_llm_schema())
            except Exception as e:
                self._send(500, {"error": f"Failed to load schemas: {e}"})
        elif self.path == "/api/events":
            self.stream_events()
        elif self.path == "/api/cameras":
            self._send(200, cameras())
        elif self.path == "/api/catalogs":
            # One camera's images/analysis/bursts/pins as a single payload.
            # The SPA fetches these as relative URLs, which nginx scopes by
            # web root — a dashboard at / has no such root, so it cannot reach
            # a camera's catalog without this endpoint. No ?camera= means
            # every camera's catalog (small, and lets a dashboard enumerate).
            if camera_id is None:
                self._send(200, {c["id"]: self._load_catalog(c["id"])
                                 for c in cameras()})
            else:
                self._send(200, self._load_catalog(camera_id))
        else:
            self._send(404, {"error": "not found"})

    # --- Server-Sent Events: push new detections / bursts to the UI ---
    @staticmethod
    def _new_entries(prev, cur):
        """Items in `cur` not in `prev`, preserving `cur`'s order. `prev` may be
        any container that supports `in` (list/set/dict)."""
        prev_set = set(prev)
        return [x for x in cur if x not in prev_set]

    # --- Clip export (download a visit as GIF/MP4) ---
    @staticmethod
    def _clip_meta(fmt):
        """(extension, content-type) for the requested format; default GIF."""
        return ("mp4", "video/mp4") if str(fmt or "").lower() == "mp4" else ("gif", "image/gif")

    @staticmethod
    def _clip_resolve(files, resolve, cap=300):
        """Map requested filenames to existing on-disk paths via `resolve`
        (find_image-like: name -> dir or None). Drops anything that doesn't
        resolve (rejects path traversal / missing), preserves order, caps count."""
        out = []
        for name in (files or [])[:cap]:
            d = resolve(name)
            if d:
                out.append(os.path.join(d, name))
        return out

    @staticmethod
    def _clip_filename(stem, ext):
        """Safe download filename: visit-style stem sanitised to [A-Za-z0-9_.-]."""
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(stem))[:60] or "visit"
        return f"{safe}.{ext}"

    def _serve_clip(self, payload):
        """POST /api/clip {files:[name,...], format:"gif"|"mp4"} -> the assembled
        clip as a download. Read-only: filenames are validated against the camera
        dirs (no traversal) and the bytes are built on the fly via media.py."""
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list) or not files:
            self._send(400, {"error": "files[] required"})
            return
        ext, ctype = self._clip_meta(payload.get("format"))
        frames = self._clip_resolve(files, find_image)
        if not frames:
            self._send(404, {"error": "no valid frames for that selection"})
            return
        import tempfile
        from integrations import media
        tmp = tempfile.NamedTemporaryFile(suffix="." + ext, delete=False)
        tmp.close()
        try:
            built = (media.build_mp4(frames, tmp.name) if ext == "mp4"
                     else media.build_gif(frames, tmp.name))
            if not built or not os.path.exists(tmp.name) or os.path.getsize(tmp.name) == 0:
                self._send(500, {"error": f"{ext} build failed (ffmpeg/PIL available?)"})
                return
            with open(tmp.name, "rb") as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition",
                             f'attachment; filename="{self._clip_filename("visit", ext)}"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._send(500, {"error": str(e)})
        finally:
            try:
                os.remove(tmp.name)
            except OSError:
                pass

    @staticmethod
    def _detection_labels(rec):
        """YOLO/detector presence keys that are True. Never HA flag names."""
        from analyze_images import YOLO_PRESENCE_KEYS
        if not isinstance(rec, dict):
            return []
        return sorted(k for k in YOLO_PRESENCE_KEYS if rec.get(k) is True)

    @staticmethod
    def _verified_detections(analysis):
        """Files carrying a final LLM verdict (_llm dict, no skip) with a label."""
        from analyze_images import is_llm_verified
        out = {}
        for f, v in analysis.items():
            if not is_llm_verified(v):
                continue
            labels = Handler._detection_labels(v)
            if labels:
                out[f] = labels
        return out

    @staticmethod
    def _preliminary_detections(analysis):
        """Detector-only hits with ≥1 YOLO True key.

        Includes rows with a ``fast_pass`` (awaiting LLM) and
        ``_llm_skip == "no_trigger"`` with a detector label (the live
        car-only shape has no fast_pass). Disjoint from
        _verified_detections. Labels are YOLO_PRESENCE_KEYS only.
        """
        from analyze_images import is_llm_verified
        out = {}
        for f, v in analysis.items():
            if not isinstance(v, dict) or is_llm_verified(v):
                continue
            if "fast_pass" not in v and v.get("_llm_skip") != "no_trigger":
                continue
            labels = Handler._detection_labels(v)
            if labels:
                out[f] = labels
        return out

    def _sse(self, event, data):
        self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def stream_events(self):
        """Long-lived text/event-stream emitting new-detection /
        detection.preliminary / new-burst as the pipeline writes analysis.json /
        bursts.json. Cheap: it polls file mtimes and only re-reads on change.
        The first pass seeds state without emitting, so a client doesn't get
        blasted with the whole backlog."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")  # ask any proxy not to buffer
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        analysis_path = os.path.join(BASE_DIR, "analysis.json")
        bursts_path = os.path.join(BASE_DIR, "bursts.json")
        seen_det, seen_prelim, seen_bursts = {}, {}, set()
        seen_files = set()
        a_mtime = b_mtime = -1.0
        seeded = False
        ev_off = 0
        try:
            import pipeline_events
            try:
                ev_off = os.path.getsize(pipeline_events.EVENTS_FILE)
            except OSError:
                ev_off = 0
        except Exception:
            pipeline_events = None
        try:
            while True:
                # Primary bus: append-only events.jsonl (pipeline emit).
                if pipeline_events is not None:
                    try:
                        ev_off, evs = pipeline_events.iter_since(ev_off)
                    except Exception:
                        evs = []
                    if seeded:
                        for ev in evs:
                            name = ev.get("event")
                            f = ev.get("file")
                            if name == "image.new" and f:
                                seen_files.add(f)
                                self._sse("image.new", {"file": f})
                            elif name == "new-detection" and f:
                                labs = ev.get("labels") or []
                                seen_det[f] = labs
                                self._sse("new-detection", {"file": f, "labels": labs})
                            elif name == "detection.preliminary" and f:
                                labs = ev.get("labels") or []
                                seen_prelim[f] = labs
                                self._sse("detection.preliminary", {
                                    "file": f, "labels": labs})
                            elif name == "new-burst":
                                bid = ev.get("id")
                                if bid:
                                    seen_bursts.add(bid)
                                self._sse("new-burst", {
                                    "id": bid,
                                    "summary": ev.get("summary") or "",
                                })
                try:
                    amt = os.path.getmtime(analysis_path)
                except OSError:
                    amt = 0.0
                if amt != a_mtime:
                    a_mtime = amt
                    try:
                        data = json.load(open(analysis_path))
                    except (OSError, ValueError):
                        data = {}
                    cur = self._verified_detections(data)
                    prelim = self._preliminary_detections(data)
                    # A frame first appearing in analysis.json (right after the
                    # fast pass) is the earliest new-frame signal the API has —
                    # the raw inotify ingest lives in the pipeline process.
                    new_files = self._new_entries(seen_files, list(data.keys()))
                    if seeded:
                        for f in new_files:
                            self._sse("image.new", {"file": f})
                        for f in cur.keys() - seen_det.keys():
                            self._sse("new-detection", {"file": f, "labels": cur[f]})
                        # Detector-only hits, surfaced before (or without) an LLM
                        # verdict. Suppress ones already promoted to verified.
                        for f in prelim.keys() - seen_prelim.keys() - cur.keys():
                            self._sse("detection.preliminary", {"file": f, "labels": prelim[f]})
                    seen_det = cur
                    seen_prelim = prelim
                    seen_files = set(data.keys())

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
                # Named heartbeat (every ~3s) the client can observe to detect a
                # silently-stalled connection (a bare ": ping" comment is invisible
                # to EventSource); also keeps proxies from buffering.
                self._sse("ping", {})
                self.wfile.flush()
                # Tail the event log often; mtime fallback still catches
                # catalogs written by an older pipeline that did not emit.
                time.sleep(1 if pipeline_events is not None else 3)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return  # client went away

    def do_POST(self):
        # Parse `?camera=<id>` off the path first, then dispatch on the bare
        # path — otherwise every scoped request falls through to 404 because
        # "/api/pin?camera=back" != "/api/pin". The write endpoints re-read
        # the param with require=True inside their branch.
        camera_id = self._camera_param()
        self.path = self.path.split("?", 1)[0]
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            filename = payload.get("filename", "")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "bad request"})
            return

        if self.path == "/api/clip":
            self._serve_clip(payload)
            return

        if self.path == "/api/settings":
            changed = {}
            for key, spec in MUTABLE_SETTINGS.items():
                if key in payload:
                    if not setting_valid(key, payload[key]):
                        if spec.get("kind") == "ignore_regions":
                            detail = "a list of {camera, polygon[3+], labels?, mode?, scan?, id?}"
                        elif "choices" in spec:
                            detail = list(spec["choices"])
                        else:
                            detail = f"{spec['min']}..{spec['max']}"
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
            try:
                save_slack_settings(changes)
            except IntegrationsUnreadable as e:
                self._send(409, {"error": str(e)})
                return
            except OSError as e:
                self._send(500, {"error": f"could not write integrations: {e}"})
                return
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
            # A pin is per-camera. `?camera=` is mandatory — pinning "all
            # cameras" as one set is exactly the leak this module exists to
            # prevent (the old single pins.json was synced to every web root).
            if camera_id is None:
                self._send(400, {"error": "camera is required for this endpoint "
                                            "(known: " +
                                            ", ".join(c["id"] for c in cameras()) + ")"})
                return
            image_dir = find_image(filename, camera_id)
            if image_dir is None:
                self._send(404, {"error": "image not found"})
                return
            pins = load_pins(camera_id)
            if payload.get("pinned"):
                pins.add(filename)
            else:
                pins.discard(filename)
            save_pins(pins, camera_id)
            self._send(200, {"ok": True, "pinned": filename in pins})

        elif self.path == "/api/delete":
            if camera_id is None:
                self._send(400, {"error": "camera is required for this endpoint "
                                            "(known: " +
                                            ", ".join(c["id"] for c in cameras()) + ")"})
                return
            image_dir = find_image(filename, camera_id)
            if image_dir is None:
                self._send(404, {"error": "image not found"})
                return
            for path in (os.path.join(image_dir, filename),
                         os.path.join(image_dir, "thumbs", filename)):
                try:
                    os.remove(path)
                except OSError:
                    pass
            pins = load_pins(camera_id)
            if filename in pins:
                pins.discard(filename)
                save_pins(pins, camera_id)
            print(f"Deleted {filename} from {image_dir}")
            self._send(200, {"ok": True})

        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass  # quiet; deletions are logged explicitly


if __name__ == "__main__":
    print(f"Webcam API listening on :{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
