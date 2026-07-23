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
import time

import requests

API = "https://slack.com/api"
TIMEOUT = 30
UPLOAD_TIMEOUT = 120
MAX_RETRY_AFTER = 10  # cap the rate-limit wait; the sweep holds the global lock
# GIFs above this are re-encoded as MP4 (ffmpeg compresses far better)
GIF_MAX_BYTES = 3 * 1024 * 1024

# Common Slack error codes mapped to actionable hints (shown in the UI's
# "Send test" toast and the pipeline log when setup is wrong).
_ERROR_HINTS = {
    "not_in_channel": "invite the bot to that channel (/invite @yourbot)",
    "channel_not_found": "channel_id not found — copy it from Slack (channel → View details)",
    "is_archived": "that channel is archived",
    "invalid_auth": "bot token rejected — re-check the xoxb- token",
    "not_authed": "no bot token was sent",
    "token_revoked": "bot token revoked — reinstall the Slack app",
    "missing_scope": "the app is missing a scope (needs chat:write and files:write)",
    "ratelimited": "Slack rate limit hit — try again shortly",
}


def _friendly(error):
    hint = _ERROR_HINTS.get(error)
    return f"{error} ({hint})" if hint else error


def _call(method, token, **kwargs):
    """POST a Slack Web API method, raising RuntimeError on failure.

    Honors one HTTP 429 retry (Slack's Retry-After) and tolerates a
    non-JSON body (error pages, gateway hiccups) instead of throwing an
    opaque JSONDecodeError.
    """
    headers = {"Authorization": f"Bearer {token}"}
    url = f"{API}/{method}"
    resp = requests.post(url, headers=headers, timeout=TIMEOUT, **kwargs)
    if resp.status_code == 429:
        wait = min(int(resp.headers.get("Retry-After", "1") or "1"), MAX_RETRY_AFTER)
        time.sleep(wait)
        resp = requests.post(url, headers=headers, timeout=TIMEOUT, **kwargs)
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"{method}: HTTP {resp.status_code} (non-JSON response)")
    if not data.get("ok"):
        raise RuntimeError(f"{method}: {_friendly(data.get('error', 'unknown error'))}")
    return data


def send_message(cfg, text, update_ts=None):
    """Post a plain text message (used for health alerts). Returns (ok, detail, ts).

    If ``update_ts`` is provided, tries ``chat.update`` first so the same
    logical alert is deduplicated in-channel (preferred for flapping conditions
    such as disk budget). Falls back to a fresh ``chat.postMessage`` when
    update is impossible or fails.
    """
    cfg = cfg or {}
    token, channel = cfg.get("bot_token"), cfg.get("channel_id")
    if not token or not channel:
        return False, "bot_token and channel_id are required", None
    try:
        if update_ts:
            try:
                data = _call("chat.update", token,
                             json={"channel": channel, "ts": update_ts, "text": text})
                return True, "updated", data.get("ts") or update_ts
            except Exception as upd_err:
                # Fall through to a fresh post if the old message is gone / uneditable
                print(f"[slack] chat.update failed ({upd_err}); posting new message")
        data = _call("chat.postMessage", token, json={"channel": channel, "text": text})
        return True, "sent", data.get("ts")
    except Exception as e:
        return False, str(e), None


def send_test_message(cfg):
    """Post a plain confirmation message to verify creds. Returns (ok, detail)."""
    ok, detail, _ts = send_message(cfg, ":white_check_mark: Webcam gallery connected to Slack.")
    return ok, ("Test message sent" if ok else detail)


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


def _share(token, channel, text, path=None, title=None):
    """Post ``text`` to ``channel``, attaching ``path`` if given.

    Degrades gracefully: if the file upload fails (e.g. the app lacks the
    ``files:write`` scope) it still posts the text + deep link via
    chat.postMessage, so a notification always lands. Returns a short detail
    string; raises only if even the text post fails (caller turns that into
    ``(False, reason)``)."""
    if path and os.path.exists(path):
        try:
            file_ref = _upload(token, path, title or os.path.basename(path))
            _call("files.completeUploadExternal", token, data={
                "files": json.dumps([file_ref]),
                "channel_id": channel,
                "initial_comment": text,
            })
            return f"posted {os.path.basename(path)}"
        except Exception as upload_err:
            _call("chat.postMessage", token, json={"channel": channel, "text": text})
            return f"posted text-only (upload failed: {upload_err})"
    _call("chat.postMessage", token, json={"channel": channel, "text": text})
    return "posted (text only)"


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
            return True, _share(token, channel, text, clip, f"Sequence {burst_id}")
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
        text += f"\n<{base}/?image={filename}|View this frame>"

    try:
        return True, _share(token, channel, text, image_path, filename)
    except Exception as e:
        return False, str(e)
