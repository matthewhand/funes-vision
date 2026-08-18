"""ntfy integration: push burst summaries / detections to an ntfy topic.

ntfy (https://ntfy.sh, self-hostable) is dead-simple HTTP pub-sub: POST a
message body to ``<server>/<topic>``; metadata rides in headers (Title, Click,
Tags, Priority). Optional bearer token for protected topics. Unlike Slack this
provider does NOT upload the image — it links back to the gallery via Click
(needs ``public_base_url``); the frame itself stays behind your reverse proxy.

Config block (the ``ntfy`` object of integrations.json):
    enabled, server_url (required; no public default), topic, token (optional),
    public_base_url (optional, for click-through), notify_mode

Mirrors slack.py's provider contract: post_burst / post_image / send_message /
send_test_message, each returning (ok, detail).
"""
import requests

TIMEOUT = 15


def _endpoint(cfg):
    """Full publish URL ``<server>/<topic>``, or '' if server_url or topic is missing."""
    cfg = cfg or {}
    server = (cfg.get("server_url") or "").strip().rstrip("/")
    topic = (cfg.get("topic") or "").strip().strip("/")
    return f"{server}/{topic}" if (server and topic) else ""


def _deep_link(cfg, key, ident):
    """Click-through URL into the gallery, or '' when no public_base_url/ident."""
    base = ((cfg or {}).get("public_base_url") or "").strip().rstrip("/")
    return f"{base}/?{key}={ident}" if (base and ident) else ""


def _headers(cfg, title=None, click=None, tags=None):
    """ntfy metadata headers (ASCII-safe; the message itself rides in the body)."""
    h = {}
    if title:
        h["Title"] = title
    if click:
        h["Click"] = click
    if tags:
        h["Tags"] = tags if isinstance(tags, str) else ",".join(tags)
    token = (cfg or {}).get("token")
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _post(cfg, body, title=None, click=None, tags=None):
    url = _endpoint(cfg)
    if not url:
        cfg = cfg or {}
        if not (cfg.get("topic") or "").strip().strip("/"):
            return False, "topic is required"
        return False, "server_url is required"
    try:
        resp = requests.post(url, data=(body or "").encode("utf-8"),
                             headers=_headers(cfg, title, click or None, tags),
                             timeout=TIMEOUT)
        if resp.status_code >= 400:
            return False, f"HTTP {resp.status_code}"
        return True, "sent"
    except Exception as e:
        return False, str(e)


def send_message(cfg, text, update_ts=None):
    """Plain push (used for health alerts). Returns (ok, detail, ts).
    ntfy has no message-update concept, so update_ts is ignored and ts is None."""
    ok, detail = _post(cfg, text, title="Webcam", tags="warning")
    return ok, detail, None


def send_test_message(cfg):
    ok, detail = _post(cfg, "Webcam gallery connected to ntfy.",
                       title="Webcam", tags="white_check_mark")
    return ok, ("Test message sent" if ok else detail)


def post_burst(cfg, burst_id, summary, frame_paths):
    """Push a burst/sequence summary with a click-through to that sequence."""
    return _post(cfg, summary, title="Webcam: sequence", tags="movie_camera",
                 click=_deep_link(cfg, "event", burst_id))


def post_image(cfg, filename, labels, caption, image_path):
    """Push a single detection with a click-through to that frame."""
    what = ", ".join(labels) if labels else "motion"
    return _post(cfg, caption or what, title=f"Webcam: {what}", tags="camera",
                 click=_deep_link(cfg, "image", filename))
