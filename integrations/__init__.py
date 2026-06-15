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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(ROOT, "integrations.json")


def load_config():
    """Read integrations.json; an absent/corrupt file means 'nothing enabled'."""
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# Notification modes (per integration, key "notify_mode"):
#   context  - only burst/sequence summaries (default; quietest)
#   objects  - every newly-analyzed frame that has a detection
#   all      - every newly-analyzed frame (detection or clear)
# Per-image modes fire only for freshly-arrived frames, never the idle
# backfill of the historical archive (that would be a flood).
DEFAULT_NOTIFY_MODE = "context"


def notify_burst(burst_id, summary, frame_paths, image_dir=None):
    """Fan a new burst summary out to enabled integrations in "context" mode.

    Fully guarded: a misconfigured or failing integration is logged and
    skipped -- it must never propagate an exception back into the analysis
    pipeline (which holds the global lock).
    """
    if not summary:
        return
    cfg = load_config()

    slack = cfg.get("slack") or {}
    if slack.get("enabled") and slack.get("notify_mode", DEFAULT_NOTIFY_MODE) == "context":
        try:
            from . import slack as slack_mod
            ok, detail = slack_mod.post_burst(slack, burst_id, summary, frame_paths)
            print(f"[integrations] slack: {detail}")
        except Exception as e:
            print(f"[integrations] slack failed: {e}")


def notify_image(filename, labels, caption, image_path, image_dir=None):
    """Fan a single freshly-analyzed frame out to enabled integrations whose
    notify_mode wants per-image alerts:
      objects -> only when ``labels`` is non-empty (a real detection)
      all     -> every frame
    "context" mode skips per-image alerts (it uses burst summaries instead).
    Fully guarded; never raises into the pipeline.
    """
    cfg = load_config()
    slack = cfg.get("slack") or {}
    if not slack.get("enabled"):
        return
    mode = slack.get("notify_mode", DEFAULT_NOTIFY_MODE)
    if mode not in ("objects", "all"):
        return
    if mode == "objects" and not labels:
        return
    try:
        from . import slack as slack_mod
        ok, detail = slack_mod.post_image(slack, filename, labels, caption, image_path)
        print(f"[integrations] slack image: {detail}")
    except Exception as e:
        print(f"[integrations] slack image failed: {e}")
