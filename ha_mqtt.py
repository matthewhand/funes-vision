"""Publish Example HA vision flags to Mosquitto for the compare dashboard.

Called from analyze_images.py after a person/dog LLM merge/skip is persisted.
Car-only (skip_reason=no_trigger) is not published as an analysis.
Secrets live in integrations.json (gitignored) or MQTT_* env vars.
"""
from __future__ import annotations

import json
import os
import re
import socket
import struct
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(ROOT, "integrations.json")
SETTINGS_FILE = os.path.join(ROOT, "settings.json")
ANALYSIS_FILE = os.path.join(ROOT, "analysis.json")

HA_CAMERA = {"front": "front_door", "back": "dog_cam"}
TOPICS = {
    "front_door": "example/vision/front_door",
    "dog_cam": "example/vision/dog_cam",
}
DEFAULT_PORT = 1883
DEFAULT_MODEL = "gemma4:e2b"
FILENAME_TS = re.compile(
    r"_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{3})_"
)

# Imported lazily so unit tests can stub camera_kind / flag keys.
def _ai():
    import analyze_images as ai
    return ai


def load_mqtt_cfg():
    """Broker settings. Env wins; then integrations.json mqtt block.

    No implicit hosts: enabled with no host/hosts/MQTT_HOST does not publish.
    """
    cfg = {}
    try:
        with open(CONFIG_FILE) as f:
            cfg = (json.load(f) or {}).get("mqtt") or {}
    except (OSError, ValueError):
        cfg = {}
    hosts = cfg.get("hosts")
    if isinstance(hosts, str):
        hosts = [hosts]
    if not isinstance(hosts, (list, tuple)) or not hosts:
        host = os.environ.get("MQTT_HOST") or cfg.get("host")
        hosts = [host] if host else []
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "hosts": [h for h in hosts if h],
        "port": int(os.environ.get("MQTT_PORT") or cfg.get("port") or DEFAULT_PORT),
        "user": os.environ.get("MQTT_USER") or cfg.get("user") or "",
        "password": os.environ.get("MQTT_PASSWORD") or cfg.get("password") or "",
        "qos": int(cfg.get("qos", 0)),
        "retain": bool(cfg.get("retain", True)),
        "client_id": cfg.get("client_id") or "example-webcam",
    }


def display_tz():
    tz_name = "Australia/Sydney"
    try:
        with open(SETTINGS_FILE) as f:
            tz_name = (json.load(f) or {}).get("timezone") or tz_name
    except (OSError, ValueError):
        pass
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name)
    except Exception:
        return None


def record_ts(img_name, image_path=None):
    """ISO timestamp from camera filename, else file mtime, else now."""
    tz = display_tz()
    m = FILENAME_TS.search(img_name or "")
    if m:
        y, mo, d, h, mi, s, ms = (int(x) for x in m.groups())
        dt = datetime(y, mo, d, h, mi, s, ms * 1000, tzinfo=tz)
        return dt.isoformat()
    if image_path and os.path.exists(image_path):
        dt = datetime.fromtimestamp(os.path.getmtime(image_path), tz=tz)
        return dt.isoformat()
    now = datetime.now(tz=tz) if tz else datetime.utcnow()
    return now.isoformat()


SKIP_ONLY_REASONS = (
    "llm_failed", "low_mem", "budget", "ollama_down", "llm_disabled",
)


def is_analysed(rec):
    """True when the record has a successful e2b/HA flag analysis."""
    import catalog
    return catalog.is_llm_verified(rec)


def should_publish(rec):
    """Retained compare-topic: successful HA flag analyses only.

    llm_failed / low_mem / budget / no_trigger must never overwrite a
    retained success — HA's compare card goes empty without flag keys.
    """
    return is_analysed(rec)


def _flag_default(schema, key):
    prop = (schema.get("properties") or {}).get(key) or {}
    if prop.get("type") == "boolean":
        return False
    enum = prop.get("enum") or []
    if "none" in enum:
        return "none"
    return ""


def ha_vision_payload(img_name, rec, image_path=None, e2b_loaded=None):
    """Build the HA ingest JSON. Does not include YOLO person/dog/car keys."""
    ai = _ai()
    kind = ai.camera_kind(image_path or img_name)
    cam = HA_CAMERA.get(kind, "front_door")
    schema = ai.BACK_SCHEMA if kind == "back" else ai.FRONT_SCHEMA
    keys = ai.BACK_FLAG_KEYS if kind == "back" else ai.FRONT_FLAG_KEYS
    skip = rec.get("_llm_skip") or ""
    llm = rec.get("_llm") if isinstance(rec.get("_llm"), dict) else {}
    analysed = is_analysed(rec)
    model = rec.get("_llm_model") or getattr(ai, "MODEL_PRIMARY", None) or DEFAULT_MODEL
    payload = {
        "camera": cam,
        "ts": record_ts(img_name, image_path),
        "model": model,
        "_llm": bool(analysed),
        "_llm_model": rec.get("_llm_model") if analysed else rec.get("_llm_model"),
        "_llm_ms": rec.get("_llm_ms") if rec.get("_llm_ms") is not None else None,
        "skip_reason": "" if analysed else skip,
        "e2b_loaded": bool(e2b_loaded) if e2b_loaded is not None else False,
    }
    if not analysed and not skip:
        payload["skip_reason"] = ""
    if analysed:
        for k in keys:
            if k in rec:
                payload[k] = rec[k]
            elif k in llm:
                payload[k] = llm[k]
            else:
                payload[k] = _flag_default(schema, k)
    return payload


def _mqtt_remaining_length(n):
    out = bytearray()
    while True:
        byte = n % 128
        n //= 128
        if n:
            byte |= 0x80
        out.append(byte)
        if not n:
            return bytes(out)


def _mqtt_str(s):
    b = (s or "").encode("utf-8")
    return struct.pack("!H", len(b)) + b


def _mqtt_connect_publish(host, port, topic, payload, user="", password="",
                          retain=True, client_id="example-webcam", timeout=5):
    """MQTT 3.1.1 QoS0 PUBLISH. Returns (ok, detail). No extra deps."""
    body = payload if isinstance(payload, (bytes, bytearray)) else payload.encode("utf-8")
    flags = 0x02  # clean session
    conn_pl = _mqtt_str(client_id)
    if user:
        flags |= 0x80
        conn_pl += _mqtt_str(user)
    if password:
        flags |= 0x40
        conn_pl += _mqtt_str(password)
    vh = _mqtt_str("MQTT") + bytes([4, flags, 0, 30])
    connect = bytes([0x10]) + _mqtt_remaining_length(len(vh) + len(conn_pl)) + vh + conn_pl

    topic_b = _mqtt_str(topic)
    pub_body = topic_b + body
    first = 0x30 | (0x01 if retain else 0x00)
    publish = bytes([first]) + _mqtt_remaining_length(len(pub_body)) + pub_body
    disconnect = bytes([0xE0, 0x00])

    s = socket.create_connection((host, int(port)), timeout=timeout)
    try:
        s.settimeout(timeout)
        s.sendall(connect)
        connack = b""
        while len(connack) < 4:
            chunk = s.recv(4 - len(connack))
            if not chunk:
                return False, f"{host}: short CONNACK"
            connack += chunk
        if connack[0] != 0x20 or connack[3] != 0x00:
            return False, f"{host}: CONNACK rc={connack[3] if len(connack) > 3 else connack!r}"
        s.sendall(publish)
        s.sendall(disconnect)
        return True, f"published {topic} @ {host}"
    finally:
        try:
            s.close()
        except OSError:
            pass


def publish_json(topic, obj, cfg=None):
    """Publish one JSON object. Tries each configured host. Never raises."""
    cfg = cfg or load_mqtt_cfg()
    if not cfg.get("enabled", False):
        return False, "mqtt disabled"
    if not cfg.get("hosts"):
        return False, "no hosts"
    payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    last = "no hosts"
    for host in cfg["hosts"]:
        try:
            ok, detail = _mqtt_connect_publish(
                host, cfg["port"], topic, payload,
                user=cfg.get("user") or "",
                password=cfg.get("password") or "",
                retain=cfg.get("retain", True),
                client_id=cfg.get("client_id") or "example-webcam",
            )
            if ok:
                return True, detail
            last = detail
        except Exception as e:
            last = f"{host}: {e}"
    return False, last


def _e2b_loaded():
    try:
        ai = _ai()
        tag = getattr(ai, "MODEL_PRIMARY", None) or DEFAULT_MODEL
        return bool(ai.ollama_model_loaded(tag))
    except Exception:
        return False


def publish_record(img_name, rec, image_path=None, e2b_loaded=None):
    """Hook: publish one persisted successful HA flag analysis. Guarded.

    Skip-only persists (llm_failed / low_mem / ...) are not published to the
    retained compare topic so they cannot clobber a good flag payload.
    """
    try:
        if not should_publish(rec):
            skip = (rec or {}).get("_llm_skip") if isinstance(rec, dict) else ""
            if skip:
                return False, f"skip: not overwriting retained flags ({skip})"
            return False, "skip: not an analysed HA flag record"
        if e2b_loaded is None:
            e2b_loaded = _e2b_loaded()
        payload = ha_vision_payload(img_name, rec, image_path, e2b_loaded=e2b_loaded)
        topic = TOPICS[payload["camera"]]
        ok, detail = publish_json(topic, payload)
        print(f"[ha_mqtt] {detail}")
        return ok, detail
    except Exception as e:
        print(f"[ha_mqtt] publish failed: {e}")
        return False, str(e)


def _watch_dirs():
    try:
        with open(SETTINGS_FILE) as f:
            return (json.load(f) or {}).get("watch_dirs") or []
    except (OSError, ValueError):
        return []


def _image_path(img_name):
    for d in _watch_dirs():
        p = os.path.join(d, img_name)
        if os.path.exists(p):
            return p
    return img_name


def _name_sort_key(name):
    m = FILENAME_TS.search(name or "")
    return m.group(0) if m else name


def latest_records(analysis_data):
    """Newest FRONT and BACK records with a successful e2b/HA flag analysis.

    Never returns llm_failed / no_trigger / low_mem rows — those used to win
    by filename recency and then overwrite retained compare payloads.
    """
    latest = {"front": None, "back": None}
    ai = _ai()
    for name, rec in (analysis_data or {}).items():
        if not should_publish(rec):
            continue
        kind = ai.camera_kind(name)
        prev = latest.get(kind)
        if prev is None or _name_sort_key(name) > _name_sort_key(prev[0]):
            latest[kind] = (name, rec)
    return latest


def publish_latest(analysis_data=None):
    """Publish the latest FRONT and BACK records so HA sensors are not empty."""
    if analysis_data is None:
        try:
            with open(ANALYSIS_FILE) as f:
                analysis_data = json.load(f)
        except (OSError, ValueError) as e:
            print(f"[ha_mqtt] cannot load analysis.json: {e}")
            return {}
    e2b = _e2b_loaded()
    results = {}
    for kind, pair in latest_records(analysis_data).items():
        if not pair:
            results[kind] = (False, "no record")
            continue
        name, rec = pair
        results[kind] = publish_record(name, rec, _image_path(name), e2b_loaded=e2b)
    return results


if __name__ == "__main__":
    out = publish_latest()
    for kind, (ok, detail) in out.items():
        print(f"{kind}: {'ok' if ok else 'FAIL'} {detail}")
    if out and not any(ok for ok, _ in out.values()):
        raise SystemExit(2)
