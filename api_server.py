#!/usr/bin/env python3
"""Tiny write API for the webcam gallery: pin and delete images.

The gallery itself is served by read-only nginx containers, so this
service is the single write channel. It runs on the host as the same
user as the analysis pipeline.

Endpoints (JSON unless noted):
  GET  /api/pins                  -> ["file1.jpg", ...]
  POST /api/pin    ?camera=<id>  {"filename": f, "pinned": true|false}
  POST /api/delete ?camera=<id>  {"filename": f}
  GET/POST /api/settings          -> MUTABLE_SETTINGS only (validated)
  GET/POST /api/integrations      -> redacted integration config
  POST /api/integrations/test     -> send a Slack test message
  GET  /api/status                -> pipeline/camera/disk/metrics snapshot
  GET  /api/health                -> {ok|degraded} for uptime monitors (200/503)
  GET  /api/inference_log         -> recent LLM audit trail
  GET  /api/taxonomy              -> canonical HA flag/label taxonomy + default TZ
  GET  /api/llm-schema            -> live prompt + front/back HA JSON schemas
  POST /api/clip                  -> GIF (player) or MP4 (API-only) from frame names
  GET  /api/events                -> SSE stream: image.new / new-detection / detection.preliminary / new-burst
"""
import base64
import hmac
import json
import os
import re
import stat
import subprocess
import threading
import time
import urllib.request
from urllib.parse import quote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from log_config import get_logger
from taxonomy import resolve_timezone

logger = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PINS_FILE = os.path.join(BASE_DIR, "pins.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
# Integration config + secrets (Slack tokens etc). Deliberately separate
# from settings.json: gitignored, mode 600, and NOT synced into the public
# nginx web roots, so bot tokens never become world-readable.
INTEGRATIONS_FILE = os.path.join(BASE_DIR, "integrations.json")

# Deployment-specific; configured in settings.json
try:
    with open(SETTINGS_FILE) as f:
        WATCH_DIRS = json.load(f).get("watch_dirs", [])
except (OSError, ValueError):
    WATCH_DIRS = []
PORT = 8190

# --- Exposure hardening (#44) ----------------------------------------------
# The write API has no auth unless a token is configured, so it binds loopback
# by default: reaching delete/settings/clip off-host used to be a one-line
# curl. Set WEBCAM_API_HOST=0.0.0.0 only behind a proxy that does its own auth.
API_HOST = os.environ.get("WEBCAM_API_HOST", "127.0.0.1")


class BlankTokenError(RuntimeError):
    """settings.json holds a whitespace-only api_token.

    Somebody clearly meant to configure auth and shipped an empty value (an
    unset `Environment=`, an unexpanded `${VAR}`). Serving that as "no auth
    configured" is the fail-open case this exists to refuse, so the write API
    stays closed until the operator puts a real token in place."""


def api_token():
    """Shared secret gating mutating endpoints, or "" when auth is disabled.

    Env WEBCAM_API_TOKEN wins; otherwise an "api_token" key in settings.json
    lets a deployment keep the secret beside the rest of its config. Unset
    (the default) preserves the localhost-only, no-auth workflow.

    Both sources are stripped *before* the truthiness test. Testing the raw
    value first meant a blank-but-present env var ("   ", from an unset
    `Environment=` or an unexpanded ${VAR}) was truthy, returned "" and
    suppressed the valid settings.json token -- an auth-off outcome from a
    deployment that had configured one. A blank token in settings.json is a
    misconfiguration, not a decision, so it raises instead (#25)."""
    tok = (os.environ.get("WEBCAM_API_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        with open(SETTINGS_FILE) as f:
            raw = json.load(f).get("api_token")
    except (OSError, ValueError, AttributeError):
        return ""
    if raw is None:
        return ""
    tok = str(raw)
    if not tok.strip():
        if tok:  # present but blank: a token was configured and lost
            raise BlankTokenError(
                f"{SETTINGS_FILE}: api_token is set but blank. Set a real token, "
                f"or remove the key to run the API unauthenticated on loopback.")
        return ""
    return tok.strip()


# Substrings that mark a settings key as a secret. /api/status is
# unauthenticated (the gallery, the watchdog and the uptime monitor all read
# it), so it is the widest possible hole: a serialized api_token hands every
# reader the key that authorizes deleting frames, rewriting settings and
# swapping the Slack bot token. Mirrors redacted_integrations() -- report that
# a secret exists, never its value.
SECRET_SETTING_MARKERS = ("token", "secret", "password", "passwd", "api_key",
                          "apikey", "private_key", "credential")


def is_secret_key(key):
    return any(m in str(key).lower() for m in SECRET_SETTING_MARKERS)


def redacted_settings(settings):
    """settings.json with every secret key dropped, for unauthenticated reads."""
    if not isinstance(settings, dict):
        return {}
    return {k: v for k, v in settings.items() if not is_secret_key(k)}


def cors_origins():
    """Allowed browser origins for CORS, comma-separated via env.

    Defaults to the local gallery. There is deliberately no wildcard: the old
    `Access-Control-Allow-Origin: *` let any page a user visited POST deletes
    at the LAN host. Read per-call so tests (and ops) can change it live."""
    raw = os.environ.get("WEBCAM_CORS_ORIGIN",
                         "http://localhost:8180,http://127.0.0.1:8180")
    # "*" is dropped even if configured: echoing it while credentials are
    # allowed is both invalid and the exact hole this replaced.
    return [o.strip() for o in raw.split(",") if o.strip() and o.strip() != "*"]


# --- SSE resource limits (#71) ---------------------------------------------
def _env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


_SSE_LOCK = threading.Lock()
_sse_active = 0


def sse_try_acquire():
    """Reserve a stream slot. False when WEBCAM_SSE_MAX_CLIENTS is reached
    (0 disables the cap). Each accepted /api/events connection holds one."""
    global _sse_active
    limit = _env_int("WEBCAM_SSE_MAX_CLIENTS", 8)
    with _SSE_LOCK:
        if limit > 0 and _sse_active >= limit:
            return False
        _sse_active += 1
        return True


def sse_release():
    global _sse_active
    with _SSE_LOCK:
        if _sse_active > 0:
            _sse_active -= 1


def sse_active():
    with _SSE_LOCK:
        return _sse_active


# --- Camera registry -------------------------------------------------------
# Single source of truth for camera dirs / labels / ids. Derived from
# `watch_dirs` so there is no second config to drift: the pipeline, the API,
# and the SPA all read the same list. Backward compatible — the registry is
# built from what is already configured, and every `?camera=` param below
# falls back to the first camera when absent (existing callers keep working).
def cameras():
    """[{id, kind, label, source_dir}] for every configured watch dir.

    Two fields, because they answer different questions:
    - `id` is the folder basename (Webcam21, Webcam22) — what the SPA uses
      for routing and what ?camera=<id> resolves against. Naming a camera
      after its folder means renaming the folder renames the camera, which
      is the intuitive behaviour and avoids the SPA hardcoding names the
      API doesn't know.
    - `kind` is front/back, from camera_kind() — what settings.json's
      ignore_regions[].camera keys use (zones.py:70) and what the pipeline
      uses for HA schema rows. Kept separate so renaming a folder does not
      silently break parked-car mute zones.
    """
    out = []
    for i, d in enumerate(WATCH_DIRS):
        base = os.path.basename(d.rstrip("/")) or d
        try:
            from analyze_images import camera_kind
            kind = camera_kind(d)
        except Exception:
            kind = "front" if i == 0 else "back"
        out.append({
            "id": base,
            "kind": kind,
            "label": base,
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


def camera_kind_for_id(camera_id):
    """kind (front/back) of a camera id, or None."""
    for c in cameras():
        if c["id"] == camera_id:
            return c["kind"]
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
    """Delivery state, with every detail re-redacted on the way out.

    A state file written before the detail redaction (or by hand) can still
    hold a Slack signed upload URL carrying the bot token and signature, and
    this is served by unauthenticated GET /api/integrations -- so redact at
    read time too, not only at write time."""
    from integrations import redact_detail
    try:
        with open(os.path.join(BASE_DIR, "integrations_state.json")) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict):
        return {}
    for entry in state.values():
        if not isinstance(entry, dict):
            continue
        for stamp in entry.values():
            if isinstance(stamp, dict) and isinstance(stamp.get("detail"), str):
                stamp["detail"] = redact_detail(stamp["detail"])
    return state


def redacted_integrations():
    """Public-safe view: presence of tokens, never their values."""
    slack = load_integrations().get("slack") or {}
    # Whether Slack has anything to post depends on the pipeline toggle in
    # settings.json (context mode only fires from burst summaries), so the UI
    # needs it — otherwise "enabled" reads as "will post" when the summaries
    # switch is off and the integration is silent.
    try:
        with open(SETTINGS_FILE) as f:
            summaries = bool(json.load(f).get("burst_summaries_enabled"))
    except (OSError, ValueError):
        summaries = False
    return {
        "slack": {
            "enabled": bool(slack.get("enabled")),
            "has_bot_token": bool(slack.get("bot_token")),
            "has_app_token": bool(slack.get("app_token")),
            "channel_id": slack.get("channel_id", ""),
            "public_base_url": slack.get("public_base_url", ""),
            "notify_mode": slack.get("notify_mode", "context"),
            "burst_summaries_enabled": summaries,
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
    # mode=0o600 is set on the temp file before the rename, so the secret is
    # never briefly world-readable: the old write-then-chmod pair left a window
    # in which a crash kept integrations.json at 0644 permanently (#32).
    _atomic_write_json(INTEGRATIONS_FILE, data, indent=2, mode=0o600)


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
                    with open(p) as f:
                        out.update(json.load(f))
                except (OSError, ValueError):
                    pass
        if not out and os.path.exists(PINS_FILE):
            try:
                with open(PINS_FILE) as f:
                    return set(json.load(f))
            except (OSError, ValueError):
                pass
        return out
    path = _pins_path(camera_id)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return set(json.load(f))
        except (OSError, ValueError):
            return set()
    # Legacy: fall back to the repo-wide pins file (pre-camera-scoping).
    if os.path.exists(PINS_FILE):
        try:
            with open(PINS_FILE) as f:
                return set(json.load(f))
        except (OSError, ValueError):
            return set()
    return set()


# Serializes every load+mutate+save of a camera's pins. Without it, N
# concurrent POST /api/pin each read the same file and the last writer wins:
# 12 concurrent pins persisted 1. A torn write used to be worse -- load_pins
# swallows ValueError, so a half-written file reads as "nothing is pinned"
# and those frames become deletable (#26).
_PINS_LOCK = threading.Lock()


def save_pins(pins, camera_id):
    """Write one camera's pins. `camera_id` is mandatory — saving "all cameras"
    as one set is exactly the leak this module exists to prevent.

    Atomic, like settings/integrations, and 0644 because nginx serves these
    read-only as www-data (mode is applied to the temp file before the rename,
    so there is no world-readable window)."""
    from analyze_images import _atomic_write_json
    _atomic_write_json(_pins_path(camera_id), sorted(pins), indent=2, mode=0o644)


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
        with open(os.path.join(BASE_DIR, "inference_log.json")) as f:
            log = json.load(f)
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


def _inotify_running():
    try:
        return subprocess.run(["pgrep", "-x", "inotifywait"],
                              capture_output=True).returncode == 0
    except OSError:
        return False


def _ollama_reachable(url):
    try:
        with urllib.request.urlopen(url, timeout=3):
            return True
    except Exception:
        return False


# --- Liveness probe cache (#33) ---------------------------------------------
# /api/status and /api/health are unauthenticated and polled hard: the SPA,
# tools/watchdog.sh and the uptime monitor all hit them. Each uncached call
# forked pgrep and opened a socket to Ollama, so 20 polls cost 20 forks and 20
# connects on the documented 4-core box, and the health endpoint the watchdog
# depends on was the one paying it. Both answers are stable for seconds.
_PROBE_LOCK = threading.Lock()
_PROBE_CACHE = {}


def _cached_probe(key, compute):
    """`compute()` at most once per WEBCAM_PROBE_TTL seconds (0 = no cache).

    Computed outside the lock: a slow Ollama probe must not serialize every
    other status request behind it."""
    ttl = _env_int("WEBCAM_PROBE_TTL", 5)
    now = time.time()
    with _PROBE_LOCK:
        hit = _PROBE_CACHE.get(key)
        if hit is not None and ttl > 0 and now - hit[0] < ttl:
            return hit[1]
    value = compute()
    with _PROBE_LOCK:
        _PROBE_CACHE[key] = (time.time(), value)
    return value


def reset_probe_cache():
    """Drop cached probe answers (tests, and ops after a pipeline restart)."""
    with _PROBE_LOCK:
        _PROBE_CACHE.clear()


def pipeline_status(camera_id=None):
    """Read-only snapshot of how the pipeline is configured and doing.

    `camera_id` scopes the per-camera fields (cameras list, images on disk,
    queue counts) to one camera. Global fields (settings, LLM, trigger,
    filesystem, timezone, inference metrics) are never scoped — they describe
    the box, not a camera. With no camera_id the status covers everything
    (backward compatible with existing callers).

    The settings block is redacted: this endpoint is unauthenticated, so the
    api_token that authorizes every write must never appear in it (#19)."""
    try:
        with open(SETTINGS_FILE) as f:
            settings = json.load(f)
    except (OSError, ValueError):
        settings = {}

    status = {"watch_dirs": WATCH_DIRS, "settings": redacted_settings(settings),
              "trigger": {"inotify_active": False,
                          "idle_sweep_seconds": settings.get("idle_sweep_seconds", 60)},
              "llm": {"model": settings.get("model_local"), "reachable": False,
                      "allow_cloud": settings.get("allow_cloud", False)}}

    status["trigger"]["inotify_active"] = _cached_probe("inotify", _inotify_running)

    url = settings.get("ollama_url", "http://localhost:11434") + "/api/version"
    status["llm"]["reachable"] = _cached_probe(("ollama", url),
                                               lambda: _ollama_reachable(url))

    # Per-camera fields: which dirs to look at for this request.
    try:
        with open(os.path.join(BASE_DIR, "analysis.json")) as f:
            analysis = json.load(f)
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
        with open(os.path.join(BASE_DIR, "inference_status.json")) as f:
            inference = json.load(f)
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

    if not isinstance(analysis, dict):
        analysis = {}
    scoped = analysis if not camera_id else {
        k: v for k, v in analysis.items() if k in files
    }
    status["queue"] = {
        "images_on_disk": len(files),
        "unanalyzed": max(0, len(files - set(scoped))),
        **queue_from_analysis(scoped),
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
        with open(os.path.join(BASE_DIR, "retention_log.json")) as f:
            rlog = json.load(f)
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
    # Per-request resource limits (#33). A stalled peer used to park a thread
    # in readline() for as long as it liked, and ThreadingHTTPServer adds one
    # thread per connection, so a handful of partial requests could starve the
    # /api/health endpoint the uptime monitor and the watchdog depend on. The
    # long-lived SSE stream is unaffected: /api/events only writes, so an idle
    # stream never trips a read timeout.
    SOCKET_TIMEOUT_S = 30
    MAX_BODY_BYTES = 1024 * 1024  # every mutating endpoint is small JSON

    @property
    def timeout(self):
        """Socket timeout (s) for one connection; env so a slow client can be
        given more headroom without a code change."""
        return _env_int("WEBCAM_API_SOCKET_TIMEOUT", self.SOCKET_TIMEOUT_S)

    def _cors(self):
        """Emit CORS headers scoped to the configured gallery origin.

        The request's Origin is echoed only when it is allowlisted; no Origin
        (same-origin / non-browser) or an unknown one gets no grant. A wildcard
        is never sent, so credentials stay safe."""
        origin = (self.headers.get("Origin") if self.headers else None) or ""
        allowed = cors_origins()
        if origin:
            if origin not in allowed:
                return
            chosen = origin
        else:
            if not allowed:
                return
            chosen = allowed[0]
        self.send_header("Access-Control-Allow-Origin", chosen)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def _authorized(self):
        """True when no token is configured, or the request presents it.

        Accepts a Bearer token, or Basic with the shared secret as either
        the username or the password (so `curl -u :SECRET` and `-u SECRET:`
        both work). Compared in constant time."""
        try:
            token = api_token()
        except BlankTokenError as e:
            # Fail closed: a blank configured token is not "no auth".
            logger.error("%s", e)
            self._send(500, {"error": "api_token is configured but blank; "
                                      "set a real token to enable the write API"})
            return False
        if not token:
            return True
        hdr = (self.headers.get("Authorization", "") if self.headers else "") or ""
        scheme, _, credential = hdr.partition(" ")
        scheme = scheme.lower()
        if scheme == "bearer":
            supplied = credential.strip()
        elif scheme == "basic":
            try:
                decoded = base64.b64decode(credential.strip()).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return False
            user, _, password = decoded.partition(":")
            supplied = password or user
        else:
            return False
        return bool(supplied) and hmac.compare_digest(
            supplied.encode("utf-8", "replace"), token.encode("utf-8", "replace"))

    def _mutation_refusal(self):
        """Cross-origin guard for every mutation, ahead of auth and parsing.

        Two independent reasons to refuse, both applying whether or not a token
        is configured:
        - an Origin outside the allowlist: some page the user visited. The
          response's missing CORS header does not undo the side effect, so the
          request itself has to be refused.
        - a browser-shaped request that is not application/json. text/plain and
          the form encodings are CORS-safelisted, so a browser sends them with
          no preflight at all; requiring JSON forces a preflight, which the
          origin allowlist above then refuses. Only checked when an Origin is
          present, so non-browser clients (curl, tools/watchdog.sh) are
          unaffected (#24).

        Returns a (code, body) to send, or None when the request may proceed."""
        origin = (self.headers.get("Origin", "") if self.headers else "") or ""
        if not origin:
            return None
        if origin not in cors_origins():
            return 403, {"error": "origin not allowed"}
        ctype = ((self.headers.get("Content-Type", "") or "").split(";")[0]
                 .strip().lower())
        if ctype != "application/json":
            return 415, {"error": "content-type must be application/json"}
        return None

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
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

        `thumbUrl` is the relative path to the camera's latest thumbnail. The
        dashboard lives at `/`, which maps to the front camera's root, so a
        back-camera card would otherwise try to load its thumb from the front
        root and show nothing. The API knows each camera's dir, so it hands
        back a path that resolves correctly wherever the SPA is served.
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
        images = out.get("images")
        if not isinstance(images, list):
            images = []
            out["images"] = images
        if images and isinstance(images[0], str):
            latest = images[0]
            out["thumbUrl"] = f"/cameras/{camera_id}/thumbs/{quote(str(latest))}"
        else:
            out["thumbUrl"] = None
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
                with open(SETTINGS_FILE) as f:
                    settings = json.load(f)
            except (OSError, ValueError):
                settings = {}
            if not isinstance(settings, dict):
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
                with open(os.path.join(BASE_DIR, "inference_log.json")) as f:
                    log = json.load(f)
            except (OSError, ValueError):
                log = []
            self._send(200, list(reversed(log[-50:])))  # newest first
        elif self.path == "/api/taxonomy":
            # Canonical HA flag / label taxonomy (taxonomy.py) so the SPA does
            # not keep its own hardcoded copies.
            import taxonomy
            self._send(200, taxonomy.payload())
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

    @staticmethod
    def _clip_width(value):
        """Requested downscale width clamped to [160, 960]; None means "use the
        builder default". Unparseable input is ignored, not an error."""
        try:
            return max(160, min(960, int(float(value))))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clip_fps(value):
        """Requested MP4 fps clamped to [0.5, 30]; None means "derive from the
        sampled cadence". Unparseable input is ignored, not an error."""
        try:
            fps = float(value)
        except (TypeError, ValueError):
            return None
        if fps != fps or fps in (float("inf"), float("-inf")) or fps <= 0:
            return None
        return max(0.5, min(30.0, fps))

    def _serve_clip(self, payload):
        """POST /api/clip {files:[name,...], format:"gif"|"mp4", width?, fps?} ->
        the assembled clip as a download. Read-only: filenames are validated
        against the camera dirs (no traversal) and the bytes are built on the fly
        via media.py. ``width``/``fps`` are optional and clamped; frames carry
        their filename-clock timestamps so playback follows the capture cadence."""
        files = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files, list) or not files:
            self._send(400, {"error": "files[] required"})
            return
        ext, ctype = self._clip_meta(payload.get("format"))
        width = self._clip_width(payload.get("width"))
        fps = self._clip_fps(payload.get("fps"))
        frames = self._clip_resolve(files, find_image)
        if not frames:
            self._send(404, {"error": "no valid frames for that selection"})
            return
        import tempfile
        from integrations import media
        timestamps = [media.parse_frame_timestamp(os.path.basename(f)) for f in frames]
        extra = {}
        if width is not None:
            extra["width"] = width
        if any(t is not None for t in timestamps):
            extra["timestamps"] = timestamps
        if ext == "mp4" and fps is not None:
            extra["fps"] = fps
        tmp = tempfile.NamedTemporaryFile(suffix="." + ext, delete=False)
        tmp.close()
        try:
            built = (media.build_mp4(frames, tmp.name, **extra) if ext == "mp4"
                     else media.build_gif(frames, tmp.name, **extra))
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
            self._cors()
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
        if event != "ping":
            self._last_event_ts = time.monotonic()
        self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def stream_events(self):
        """Admit a bounded number of concurrent SSE streams (#71).

        Past WEBCAM_SSE_MAX_CLIENTS the caller gets 503 instead of another
        unbounded thread, so a page that reconnects in a loop cannot pin the
        box. The slot is always released, even on a write error."""
        if not sse_try_acquire():
            self._send(503, {"error": "too many event streams; retry later",
                             "limit": _env_int("WEBCAM_SSE_MAX_CLIENTS", 8)})
            return
        try:
            self._run_event_stream()
        finally:
            sse_release()

    def _run_event_stream(self):
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
        self._cors()
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
        heartbeat_s = max(1, _env_int("WEBCAM_SSE_HEARTBEAT_S", 15))
        idle_s = _env_int("WEBCAM_SSE_IDLE_TIMEOUT_S", 600)
        self._last_event_ts = time.monotonic()
        last_heartbeat = self._last_event_ts
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
                        with open(analysis_path) as f:
                            data = json.load(f)
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
                        with open(bursts_path) as f:
                            bursts = json.load(f)
                    except (OSError, ValueError):
                        bursts = {}
                    if seeded:
                        for bid in bursts.keys() - seen_bursts:
                            self._sse("new-burst", {"id": bid,
                                                    "summary": bursts.get(bid, {}).get("summary", "")})
                    seen_bursts = set(bursts)

                seeded = True
                now = time.monotonic()
                # Named heartbeat the client can observe to detect a silently
                # stalled connection (a bare ": ping" comment is invisible to
                # EventSource); also keeps proxies from buffering. Decoupled
                # from the poll interval so idle ticks stay quiet.
                if now - last_heartbeat >= heartbeat_s:
                    self._sse("ping", {})
                    last_heartbeat = now
                # Reap connections that saw no traffic for too long, bounding
                # lifetime CPU/memory. 0 disables the idle timeout.
                if idle_s > 0 and now - self._last_event_ts >= idle_s:
                    self._sse("close", {"reason": "idle_timeout"})
                    return
                # Tail the event log often; mtime fallback still catches
                # catalogs written by an older pipeline that did not emit.
                time.sleep(1 if pipeline_events is not None else 3)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return  # client went away

    def do_POST(self):
        # Every POST mutates state or spends resources (settings/integrations/
        # clip/pin/delete), so gate them all before any parsing when a token is
        # configured. GETs stay open for the read-only gallery. The origin /
        # content-type guard comes first: a cross-origin write is refused on
        # its own merits, token or no token.
        refusal = self._mutation_refusal()
        if refusal is not None:
            self._send(*refusal)
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        # Parse `?camera=<id>` off the path first, then dispatch on the bare
        # path — otherwise every scoped request falls through to 404 because
        # "/api/pin?camera=back" != "/api/pin". The write endpoints re-read
        # the param with require=True inside their branch.
        camera_id = self._camera_param()
        self.path = self.path.split("?", 1)[0]
        if camera_id is self._BAD_CAMERA:
            # _camera_param already sent the 400; mirror the do_GET guard so an
            # unknown ?camera= doesn't fall through to a second response write.
            return
        # Validate the declared body length before reading any of it: an
        # unvalidated 2000000000 buffers 2 GB of RAM in this thread, and -1
        # parks the reader waiting for bytes that never come (#33).
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            self._send(400, {"error": "bad request"})
            return
        max_body = _env_int("WEBCAM_API_MAX_BODY", self.MAX_BODY_BYTES)
        if length < 0 or length > max_body:
            self._send(413, {"error": "request body too large"})
            return
        try:
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
                with open(SETTINGS_FILE) as f:
                    settings = json.load(f)
            except FileNotFoundError:
                settings = {}
            except (OSError, ValueError) as e:
                self._send(409, {"error": (
                    f"settings.json is unreadable; refusing to overwrite ({e})"
                )})
                return
            if not isinstance(settings, dict):
                self._send(409, {"error":
                    "settings.json is unreadable; refusing to overwrite"})
                return
            settings.update(changed)
            try:
                from analyze_images import _atomic_write_json
                # 0600: settings.json can hold api_token, and it used to be
                # written world-readable with no repair step at all (#32).
                _atomic_write_json(SETTINGS_FILE, settings, indent=2, mode=0o600)
            except OSError as e:
                self._send(500, {"error": f"could not write settings: {e}"})
                return
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
            with _PINS_LOCK:
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
                except FileNotFoundError:
                    logger.debug("delete: %s already gone", path)
                except OSError as e:
                    logger.warning("delete: could not remove %s: %s", path, e)
            # Same lock as /api/pin: unpinning is the same load+mutate+save,
            # so a delete racing a pin must not lose one.
            with _PINS_LOCK:
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


def harden_secret_files():
    """Tighten perms on the two config files that can hold secrets.

    A deployment that predates the 0600 writes (or one whose settings.json was
    copied in by a deploy script) still has api_token readable by every local
    user, and nothing repaired it. Best effort: a file we do not own is
    reported, not fatal."""
    for path in (SETTINGS_FILE, INTEGRATIONS_FILE):
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except OSError:
            continue
        if not mode & 0o077:
            continue
        try:
            os.chmod(path, 0o600)
            logger.warning("tightened %s from %04o to 0600", path, mode)
        except OSError as e:
            logger.warning("could not tighten %s from %04o: %s", path, mode, e)


if __name__ == "__main__":
    harden_secret_files()
    try:
        auth = "auth=token" if api_token() else "auth=off"
    except BlankTokenError as e:
        # Fail closed, loudly: writes stay closed until this is fixed.
        print(f"Webcam API listening on {API_HOST}:{PORT} (auth=broken)")
        print(f"ERROR: {e}")
        raise SystemExit(1)
    print(f"Webcam API listening on {API_HOST}:{PORT} ({auth})")
    ThreadingHTTPServer((API_HOST, PORT), Handler).serve_forever()
