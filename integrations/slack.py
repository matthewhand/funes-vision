"""Slack integration: post burst summaries with an animation + a deep link.

Uses Slack's current file-upload flow (``files.getUploadURLExternal`` ->
upload the bytes -> ``files.completeUploadExternal``); the legacy
``files.upload`` is retired. Posting needs only the bot token (``xoxb-``)
and a channel id. The app-level token (``xapp-``) is stored for future
Socket Mode / interactive features but is not required to post.

Config block (the ``slack`` object of integrations.json):
    enabled, bot_token, app_token, channel_id, public_base_url
"""
import json
import os
import tempfile

import requests

API = "https://slack.com/api"
TIMEOUT = 30
UPLOAD_TIMEOUT = 120
# GIFs above this are re-encoded as MP4 (ffmpeg compresses far better)
GIF_MAX_BYTES = 3 * 1024 * 1024


def _call(method, token, **kwargs):
    """POST a Slack Web API method, raising on a non-ok response."""
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(f"{API}/{method}", headers=headers, timeout=TIMEOUT, **kwargs)
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"{method}: {data.get('error', 'unknown error')}")
    return data


def send_test_message(cfg):
    """Post a plain confirmation message to verify creds. Returns (ok, detail)."""
    cfg = cfg or {}
    token, channel = cfg.get("bot_token"), cfg.get("channel_id")
    if not token or not channel:
        return False, "bot_token and channel_id are required"
    try:
        _call("chat.postMessage", token, json={
            "channel": channel,
            "text": ":white_check_mark: Webcam gallery connected to Slack.",
        })
        return True, "Test message sent"
    except Exception as e:
        return False, str(e)


def _upload(token, path, title):
    """Run the three-step external upload; returns the file ref for completion."""
    size = os.path.getsize(path)
    name = os.path.basename(path)
    info = _call("files.getUploadURLExternal", token,
                 data={"filename": name, "length": str(size)})
    with open(path, "rb") as f:
        up = requests.post(info["upload_url"], files={"file": (name, f)},
                           timeout=UPLOAD_TIMEOUT)
    up.raise_for_status()
    return {"id": info["file_id"], "title": title}


def post_burst(cfg, burst_id, summary, frame_paths):
    """Build an animation from ``frame_paths`` and post it with summary + link.

    Returns (ok, detail); never raises -- any failure is reported as
    ``(False, reason)`` so the pipeline carries on.
    """
    cfg = cfg or {}
    token, channel = cfg.get("bot_token"), cfg.get("channel_id")
    if not token or not channel:
        return False, "bot_token and channel_id are required"

    base = (cfg.get("public_base_url") or "").rstrip("/")
    link = f"{base}/?event={burst_id}" if base else ""
    text = f"*Webcam activity*\n{summary}"
    if link:
        text += f"\n<{link}|View this sequence on the timeline>"

    from . import media
    try:
        with tempfile.TemporaryDirectory() as tmp:
            clip = media.build_gif(frame_paths, os.path.join(tmp, "clip.gif"))
            if not clip or os.path.getsize(clip) > GIF_MAX_BYTES:
                mp4 = media.build_mp4(frame_paths, os.path.join(tmp, "clip.mp4"))
                if mp4:
                    clip = mp4

            if clip:
                file_ref = _upload(token, clip, f"Sequence {burst_id}")
                _call("files.completeUploadExternal", token, data={
                    "files": json.dumps([file_ref]),
                    "channel_id": channel,
                    "initial_comment": text,
                })
                return True, f"posted {os.path.basename(clip)}"

            # No animation could be built -- still post the summary + link
            _call("chat.postMessage", token, json={"channel": channel, "text": text})
            return True, "posted (text only)"
    except Exception as e:
        return False, str(e)


def post_image(cfg, filename, labels, caption, image_path):
    """Post a single frame with its detected labels + AI caption + a deep link.

    Used by the per-image notify modes (objects / all). Returns (ok, detail);
    never raises.
    """
    cfg = cfg or {}
    token, channel = cfg.get("bot_token"), cfg.get("channel_id")
    if not token or not channel:
        return False, "bot_token and channel_id are required"

    base = (cfg.get("public_base_url") or "").rstrip("/")
    label_str = ", ".join(labels) if labels else "no objects"
    text = f"*Webcam:* {label_str}"
    if caption:
        text += f"\n_{caption}_"
    if base:
        text += f"\n<{base}/|Open the gallery>"

    try:
        if image_path and os.path.exists(image_path):
            file_ref = _upload(token, image_path, filename)
            _call("files.completeUploadExternal", token, data={
                "files": json.dumps([file_ref]),
                "channel_id": channel,
                "initial_comment": text,
            })
            return True, f"posted {filename}"
        _call("chat.postMessage", token, json={"channel": channel, "text": text})
        return True, "posted (text only)"
    except Exception as e:
        return False, str(e)
