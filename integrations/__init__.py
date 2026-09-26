"""Outbound integrations for the webcam gallery.

A single hook -- ``notify_burst()`` -- is called by the analysis pipeline
each time a new burst (frame-sequence) summary is produced. That summary is
the gallery's "contextual analysis": a narrative of what happened across a
run of frames, paired with the frames themselves.

Configuration and secrets live in ``integrations.json`` at the repo root.
That file is gitignored and is NOT among the artifacts create-index.sh syncs
into the public nginx web roots, so bot tokens never become world-readable.
Each integration is opt-in via its own ``enabled`` flag.

Adding an integration (contributions welcome -- see DEVELOP.md, PR required):
expose ``post_burst(cfg, burst_id, summary, frame_paths)`` from a new module
here, then add an ``enabled`` dispatch block in ``notify_burst`` below.
"""
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(ROOT, "integrations.json")
# Delivery observability (read by api_server for the Integrations panel)
STATE_FILE = os.path.join(ROOT, "integrations_state.json")


def load_config():
    """Read integrations.json; an absent/corrupt file means 'nothing enabled'."""
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _record_delivery(name, kind, ok, detail):
    """Persist the last send result per integration so the UI can show
    'last delivered / last error' for the channel you rely on while away."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                state = json.load(f)
        else:
            state = {}
    except (OSError, ValueError):
        state = {}
    entry = state.get(name) or {}
    stamp = {"ts": time.time(), "kind": kind, "ok": bool(ok), "detail": (detail or "")[:200]}
    entry["last_delivery"] = stamp
    entry["last_error" if not ok else "last_ok"] = stamp
    state[name] = entry
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=1)
    except OSError:
        pass


# Notification modes (per integration, key "notify_mode"):
#   context  - only burst/sequence summaries (default; quietest)
#   objects  - every newly-analyzed frame that has a detection
#   all      - every newly-analyzed frame (detection or clear)
# Per-image modes fire only for freshly-arrived frames, never the idle
# backfill of the historical archive (that would be a flood).
DEFAULT_NOTIFY_MODE = "context"

# Registered providers (each is a module exposing post_burst / post_image /
# send_message / send_test_message returning (ok, detail)). Add a module here
# and it fans out everywhere — see DEVELOP.md "The contract".
PROVIDERS = ("slack", "ntfy")


def _provider(name):
    from importlib import import_module
    return import_module(f".{name}", __package__)


def _enabled_providers(cfg):
    return [(n, cfg.get(n) or {}) for n in PROVIDERS if (cfg.get(n) or {}).get("enabled")]


def notify_burst(burst_id, summary, frame_paths):
    """Fan a new burst summary out to enabled integrations in "context" mode.

    Fully guarded: a misconfigured or failing integration is logged and
    skipped -- it must never propagate an exception back into the analysis
    pipeline (which holds the global lock).
    """
    if not summary:
        return
    for name, c in _enabled_providers(load_config()):
        if c.get("notify_mode", DEFAULT_NOTIFY_MODE) != "context":
            continue
        try:
            ok, detail = _provider(name).post_burst(c, burst_id, summary, frame_paths)
            _record_delivery(name, "burst", ok, detail)
            print(f"[integrations] {name}: {detail}")
        except Exception as e:
            _record_delivery(name, "burst", False, str(e))
            print(f"[integrations] {name} failed: {e}")


def notify_alert(message, update_ts=None):
    """Push an operational health alert (text-only) to enabled integrations.
    Independent of notify_mode - alerts are about the pipeline, not detections.

    ``update_ts`` (Slack message timestamp) is forwarded so providers that
    support it can edit an existing message instead of posting a duplicate.
    Returns (any_ok, last_ts) where last_ts is the Slack ts of the message
    that was posted or updated (or None).
    """
    any_ok = False
    last_ts = None
    for name, c in _enabled_providers(load_config()):
        try:
            result = _provider(name).send_message(c, message, update_ts=update_ts)
            # Back-compat: older providers may still return (ok, detail)
            if len(result) == 3:
                ok, detail, ts = result
            else:
                ok, detail = result
                ts = None
            _record_delivery(name, "alert", ok, detail)
            any_ok = any_ok or ok
            if ok and ts:
                last_ts = ts
        except Exception as e:
            _record_delivery(name, "alert", False, str(e))
            print(f"[integrations] {name} alert failed: {e}")
    return any_ok, last_ts


def notify_image(filename, labels, caption, image_path):
    """Fan a single freshly-analyzed frame out to enabled integrations whose
    notify_mode wants per-image alerts:
      objects -> only when ``labels`` is non-empty (a real detection)
      all     -> every frame
    "context" mode skips per-image alerts (it uses burst summaries instead).
    Fully guarded; never raises into the pipeline.
    """
    for name, c in _enabled_providers(load_config()):
        mode = c.get("notify_mode", DEFAULT_NOTIFY_MODE)
        if mode not in ("objects", "all"):
            continue
        if mode == "objects" and not labels:
            continue
        try:
            ok, detail = _provider(name).post_image(c, filename, labels, caption, image_path)
            _record_delivery(name, "image", ok, detail)
            print(f"[integrations] {name} image: {detail}")
        except Exception as e:
            _record_delivery(name, "image", False, str(e))
            print(f"[integrations] {name} image failed: {e}")
