import os
import requests
import json
import base64
import math
import sys
try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None
import time
import random
import bisect
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import re
import fcntl

from log_config import get_logger

logger = get_logger(__name__)

# CONFIGURATION (defaults; override in settings.json next to this script)
MODEL_CLOUD = "google/gemma-4-31b-it"
MODEL_LOCAL = "gemma4:e2b"  # Ollama tag; settings.json model_primary overrides
# Primary/fallback inference, both via Ollama (:11434). The primary may be a
# fast cloud model (e.g. minimax-m3:cloud); the fallback a local/private model
# (e.g. gemma4:e4b). On failure or rate-limit the chain falls through.
MODEL_PRIMARY = MODEL_LOCAL  # default; overridden by settings `model_primary`
MODEL_FALLBACK = ""          # optional; settings `model_fallback`
BURST_THRESHOLD_SECONDS = 300  # Group images within 5 mins
MIN_MEM_FOR_LOCAL_GB = 6.0
MAX_AGE_DAYS = 30   # retention: unpinned non-timeline images older than this
MAX_DIR_GB = 5.0    # retention: per-camera disk budget (images + thumbs)
PERSIST_BUDGET_PCT = 20.0  # max % of max_dir_gb for LLM timeline frames past max_age_days
ALLOW_CLOUD = False  # kill switch for ALL cloud inference: OpenRouter fallback
                     # AND Ollama ':cloud' models (both ship frames off-box)
OLLAMA_URL = "http://localhost:11434"
# Idle unload: each /api/chat refreshes this TTL. Ollama's default is 5m.
OLLAMA_KEEP_ALIVE = "24h"
MAX_DEEP_PASSES = 30  # LLM calls (local or cloud) per camera per sweep
DEEP_CONCURRENCY = 1   # parallel backfill deep passes; >1 only sane for a cloud
                       # model (no local RAM contention). Rate-limit backoff guards it.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FAST_PASS_ENGINE = "yolo"  # "yolo" (recommended) or "haar" (legacy cascades)
YOLO_DIR = os.path.join(BASE_DIR, "models", "yolo")
YOLO_CONF = 0.45
DEEP_BACKFILL = False  # idle archive verification; off unless settings enable it
DEEP_PASSES_ENABLED = True  # master switch for ALL Gemma/LLM work (priority + backfill + bursts);
                            # set false to run detector-only and free CPU/RAM
BURST_SUMMARIES_ENABLED = False  # multi-image (burst) LLM captions; off by default —
                                  # e2b 400s on multi-frame chat, and single-frame analysis is enough
# Multi-image timeline context for priority scans. Off by default (memory
# pressure). When on, prior frames from the same camera are prepended to the
# vision message so e2b can see motion/context across time:
#   2 images if a prior frame exists within MULTI_IMAGE_2H minutes
#   3 images if a prior frame exists within MULTI_IMAGE_3H minutes
# Always defaults to 1 image (current frame only) when no prior exists.
MULTI_IMAGE_ENABLED = False
MULTI_IMAGE_2H = 5.0   # minutes: include 1 prior → 2 total
MULTI_IMAGE_3H = 10.0  # minutes: include 2 prior → 3 total
WATCH_DIRS = []  # REQUIRED via settings.json watch_dirs - deployment specific
# Explicit camera registry from settings.json `cameras[]`. When present it is
# the source of truth for camera identity/kind; when empty, camera_kind()
# falls back to the legacy filename/IP heuristic. Each entry is
# {id, label, kind, dir} — see settings.example.json and DEVELOP.md.
CAMERAS = []
# Labels that alone do NOT trigger an urgent deep pass (e.g. a car parked
# in frame 24/7). Persisted as _llm_skip=no_trigger (not partial — that
# would re-queue as urgent). Idle backfill verifies them later if enabled.
GATE_IGNORE_LABELS = ["car"]
# Spatial ignore: drop a YOLO hit whose box centre sits in a polygon
# (the parked orange SUV bay on the front camera). Empty = no mask.
IGNORE_REGIONS = []
# Slack "camera offline?" and /api/status stale share this default.
CAMERA_OFFLINE_HOURS_DEFAULT = 24

_settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
# Global pipeline lock. Every entry path (full sweep, --retention-only,
# --rescan-days) serialises on it so retention can never delete a frame out
# from under a live sweep, and two runs never race the catalog flush (#30).
PIPELINE_LOCK = "/tmp/webcam_analysis.lock"
# Last settings mapping applied. Kept as a mutable module global because
# configured_tz_name() and tests read it directly.
_s = {}


class SettingsError(Exception):
    """settings.json exists but could not be read or parsed as a JSON object.

    Deliberately distinct from the two *benign* unconfigured states: a missing
    settings.json (fresh install) and a valid object that simply has no
    ``watch_dirs`` yet. Only this one means the box is misconfigured, which the
    caller must not paper over (#27).
    """


def load_settings(path=None, strict=False):
    """Read settings.json into a dict.

    Lenient by default: a missing/unreadable/non-object file degrades to ``{}``
    with a warning, because ``apply_settings()`` runs at *import* time and
    raising there would take the whole module down before the CLI could report
    anything (#28).

    ``strict=True`` raises ``SettingsError`` instead of degrading, and is what
    the sweep entry point uses. A half-written settings.json (interrupted
    write, full disk, an editor truncating on save) used to be indistinguishable
    from an unconfigured one, so the sweep scanned nothing, deleted nothing,
    fired no health alert and still exited 0 -- retention was dead and the disk
    grew without bound while create-index.sh touched the success marker and the
    watchdog read the box as healthy (#27). Failing loud is the only honest
    answer; the caller then declines to analyse and declines to delete.
    """
    path = path if path is not None else _settings_path
    try:
        with open(path) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        # Fresh install: nothing configured yet is not a fault.
        return {}
    except (OSError, ValueError) as e:
        if strict:
            raise SettingsError(f"could not read settings {path}: {e}") from e
        logger.warning("could not read settings %s (%s); using current values", path, e)
        return {}
    if not isinstance(cfg, dict):
        if strict:
            raise SettingsError(f"settings {path} is not a JSON object")
        logger.warning("settings %s is not a JSON object; using current values", path)
        return {}
    return cfg


def setting_num(cfg, key, current, cast=float, lo=None, hi=None):
    """Coerce one numeric settings.json key. Never raises.

    settings.json is user-writable (via /api/settings) and survives restarts,
    so a single hostile value -- ``null``, ``"abc"``, ``{}``, ``"5.0"`` -- must
    never kill the pipeline. ``apply_settings`` runs at import *and* at the top
    of every sweep, so an unguarded ``float()`` there raised TypeError at
    import (rc=1, no output) or mid-sweep, and retention, catalog pruning and
    the watchdog path never ran: the disk then grew unbounded (#28).

    Mirrors the max_scans_per_image guard: log the bad value and keep the
    previous one. An absent key keeps *current* too, so a reload never
    clobbers a value the file does not mention. ``lo``/``hi`` clamp the
    result. bools are rejected -- True is not a disk budget.
    """
    if key not in cfg:
        return current
    raw = cfg[key]
    try:
        if isinstance(raw, bool):
            raise TypeError("bool is not a usable number here")
        val = cast(raw)
    except (TypeError, ValueError, OverflowError):
        logger.warning("ignoring invalid %s=%r; keeping %r", key, raw, current)
        return current
    if isinstance(val, float) and not math.isfinite(val):
        logger.warning("ignoring non-finite %s=%r; keeping %r", key, raw, current)
        return current
    if lo is not None and val < lo:
        logger.warning("%s=%r is below %r; clamping", key, raw, lo)
        val = lo
    if hi is not None and val > hi:
        logger.warning("%s=%r is above %r; clamping", key, raw, hi)
        val = hi
    return val


def apply_settings(cfg=None):
    """Merge a settings mapping into the runtime globals.

    Called once at import to seed defaults and again at the start of every
    sweep, so a value changed through /api/settings takes effect on the next
    sweep without a restart. A key absent from *cfg* keeps the current value,
    preserving the existing default and precedence rules. Returns the applied
    mapping.
    """
    global BURST_THRESHOLD_SECONDS, MAX_AGE_DAYS, MAX_DIR_GB
    global PERSIST_BUDGET_PCT, MIN_MEM_FOR_LOCAL_GB, ALLOW_CLOUD
    global OLLAMA_URL, OLLAMA_KEEP_ALIVE, MAX_DEEP_PASSES, DEEP_CONCURRENCY
    global MODEL_LOCAL, MODEL_PRIMARY, MODEL_FALLBACK, FAST_PASS_ENGINE
    global DEEP_BACKFILL, DEEP_PASSES_ENABLED, BURST_SUMMARIES_ENABLED
    global MULTI_IMAGE_ENABLED, MULTI_IMAGE_2H, MULTI_IMAGE_3H
    global GATE_IGNORE_LABELS, IGNORE_REGIONS, WATCH_DIRS, CAMERAS, YOLO_DIR
    global _s

    if cfg is None:
        cfg = load_settings()
    if not isinstance(cfg, dict):
        cfg = {}
    _s = cfg

    BURST_THRESHOLD_SECONDS = setting_num(cfg, "burst_threshold_seconds",
                                         BURST_THRESHOLD_SECONDS, lo=0.0)
    MAX_AGE_DAYS = setting_num(cfg, "max_age_days", MAX_AGE_DAYS, lo=0.0)
    # Clamped to >= 0. A negative budget is read as "over budget" by every
    # retention pass, and every category is evictable, so one sweep emptied
    # the camera dir (#22). 0 now means "no disk budget" (eviction skipped),
    # never "delete until empty".
    MAX_DIR_GB = setting_num(cfg, "max_dir_gb", MAX_DIR_GB, lo=0.0)
    PERSIST_BUDGET_PCT = setting_num(cfg, "persist_budget_pct", PERSIST_BUDGET_PCT,
                                    lo=0.0, hi=100.0)
    MIN_MEM_FOR_LOCAL_GB = setting_num(cfg, "min_mem_for_local_gb",
                                       MIN_MEM_FOR_LOCAL_GB, lo=0.0)
    ALLOW_CLOUD = cfg.get("allow_cloud", ALLOW_CLOUD)
    OLLAMA_URL = cfg.get("ollama_url", OLLAMA_URL)
    OLLAMA_KEEP_ALIVE = str(cfg.get("ollama_keep_alive", OLLAMA_KEEP_ALIVE) or "24h")
    MAX_DEEP_PASSES = setting_num(cfg, "max_deep_passes", MAX_DEEP_PASSES, cast=int, lo=0)
    DEEP_CONCURRENCY = setting_num(cfg, "deep_concurrency", DEEP_CONCURRENCY, cast=int, lo=0)
    MODEL_LOCAL = cfg.get("model_local", MODEL_LOCAL)
    MODEL_PRIMARY = cfg.get("model_primary", MODEL_LOCAL)  # default to model_local
    MODEL_FALLBACK = cfg.get("model_fallback", "")
    FAST_PASS_ENGINE = cfg.get("fast_pass_engine", FAST_PASS_ENGINE)
    DEEP_BACKFILL = cfg.get("deep_backfill", DEEP_BACKFILL)
    DEEP_PASSES_ENABLED = cfg.get("deep_passes_enabled", DEEP_PASSES_ENABLED)
    BURST_SUMMARIES_ENABLED = cfg.get("burst_summaries_enabled", BURST_SUMMARIES_ENABLED)
    MULTI_IMAGE_ENABLED = cfg.get("multi_image_enabled", MULTI_IMAGE_ENABLED)
    MULTI_IMAGE_2H = setting_num(cfg, "multi_image_2h_minutes", MULTI_IMAGE_2H, lo=0.0)
    MULTI_IMAGE_3H = setting_num(cfg, "multi_image_3h_minutes", MULTI_IMAGE_3H, lo=0.0)
    GATE_IGNORE_LABELS = cfg.get("gate_ignore_labels", GATE_IGNORE_LABELS)
    # `or []` semantics: preserve the empty default and let an explicit
    # null/[] in settings.json clear the list, but never clobber a value on
    # reload merely because the key was omitted.
    if "ignore_regions" in cfg:
        IGNORE_REGIONS = cfg.get("ignore_regions") or []
    WATCH_DIRS = cfg.get("watch_dirs", WATCH_DIRS)
    if "cameras" in cfg:
        CAMERAS = cfg.get("cameras") or []
    YOLO_DIR = cfg.get("yolo_dir", YOLO_DIR)

    try:
        import scans as _scans_mod
        _scans_mod.MAX_SCANS_PER_IMAGE = setting_num(
            cfg, "max_scans_per_image", _scans_mod.MAX_SCANS_PER_IMAGE, cast=int, lo=0)
    except ImportError as e:
        logger.warning("could not apply max_scans_per_image: %s", e)

    return cfg


apply_settings()

# Haar is legacy fallback only; do not fail import if OpenCV is missing.
face_cascade = body_cascade = cat_cascade = None
if cv2 is not None:
    try:
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        body_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_fullbody.xml')
        cat_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalcatface.xml')
    except Exception as e:
        print(f"Haar cascades unavailable ({e}); YOLO-only fast pass")

# Distinct from {} (no detections). Falsy so the persist path does not treat
# it as a hit. Truncated/in-flight JPEGs must not become fast_pass=negative.
class _FastPassUnread:
    __slots__ = ()
    def __bool__(self):
        return False

FAST_PASS_UNREADABLE = _FastPassUnread()


def negative_fast_pass_row(fp_results):
    """Catalog row for empty detector, or None to skip persist (imread failed)."""
    if fp_results is FAST_PASS_UNREADABLE:
        return None
    return {"fast_pass": "negative"}


def fast_pass(image_path):
    try:
        if cv2 is None or face_cascade is None or body_cascade is None or cat_cascade is None:
            return FAST_PASS_UNREADABLE
        img = cv2.imread(image_path)
        if img is None:
            return FAST_PASS_UNREADABLE
        h, w = img.shape[:2]
        scale = 400.0 / w
        small = cv2.resize(img, (400, int(h * scale)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        
        results = {}
        # Increased sensitivity: lower minNeighbors, smaller minSize
        faces = face_cascade.detectMultiScale(gray, 1.1, 3, minSize=(20, 20))
        if len(faces) > 0:
            results["face"] = True
            
        bodies = body_cascade.detectMultiScale(gray, 1.1, 2, minSize=(40, 80))
        if len(bodies) > 0:
            results["body"] = True
            
        cats = cat_cascade.detectMultiScale(gray, 1.1, 2, minSize=(20, 20))
        if len(cats) > 0:
            results["cat"] = True
            
        return results
    except Exception as e:
        print(f"Fast pass error: {e}")
        return FAST_PASS_UNREADABLE

# --- YOLO fast pass (yolov4-tiny via OpenCV DNN, COCO classes) ---
YOLO_CLASSES = {0: "person", 2: "car", 14: "bird", 15: "cat", 16: "dog"}
_yolo_model = None

def _load_yolo():
    global _yolo_model
    if _yolo_model is None:
        net = cv2.dnn.readNetFromDarknet(
            os.path.join(YOLO_DIR, "yolov4-tiny.cfg"),
            os.path.join(YOLO_DIR, "yolov4-tiny.weights"))
        model = cv2.dnn_DetectionModel(net)
        model.setInputParams(size=(416, 416), scale=1/255.0, swapRB=True)
        _yolo_model = model
    return _yolo_model

def fast_pass_yolo(image_path):
    """Returns detected labels dict, or None if YOLO itself is unavailable
    (caller falls back to Haar cascades)."""
    if cv2 is None:
        return FAST_PASS_UNREADABLE
    try:
        img = cv2.imread(image_path)
        if img is None:
            return FAST_PASS_UNREADABLE
        h, w = img.shape[:2]
        ids, confs, boxes = _load_yolo().detect(img, confThreshold=YOLO_CONF, nmsThreshold=0.4)
        results = {}
        if len(ids) == 0:
            return results
        ids = np.array(ids).flatten()
        boxes = np.array(boxes)
        kind = camera_kind(image_path)
        try:
            import zones
        except Exception as e:
            logger.debug("zones unavailable (%s); spatial ignore regions disabled", e)
            zones = None
        centres = {}
        best_area = {}
        for i, cid in enumerate(ids):
            label = YOLO_CLASSES.get(int(cid))
            if not label:
                continue
            x, y, bw, bh = [float(v) for v in boxes[i]]
            cx = (x + bw / 2.0) / w if w else 0.0
            cy = (y + bh / 2.0) / h if h else 0.0
            if zones is not None and IGNORE_REGIONS and w > 0 and h > 0:
                if zones.detection_ignored(label, cx, cy, kind, IGNORE_REGIONS):
                    continue
            results[label] = True
            area = (bw / w if w else 0.0) * (bh / h if h else 0.0)
            if label not in centres or area > best_area.get(label, -1):
                centres[label] = (round(cx, 4), round(cy, 4))
                best_area[label] = area
        if centres:
            results["_centres"] = centres
        return results
    except Exception as e:
        print(f"YOLO fast pass error: {e}")
        return None

def fast_pass_dispatch(image_path):
    if FAST_PASS_ENGINE == "yolo":
        results = fast_pass_yolo(image_path)
        if results is not None:
            return results
        print("YOLO unavailable; falling back to Haar cascades")
    return fast_pass(image_path)

LLM_STILL_WIDTH = 1280

def encode_image(image_path):
    """Full still as JPEG, resized to ~1280 wide (never a YOLO crop)."""
    try:
        img = cv2.imread(image_path)
        if img is not None:
            h, w = img.shape[:2]
            if w > LLM_STILL_WIDTH:
                nh = max(1, int(round(h * LLM_STILL_WIDTH / float(w))))
                img = cv2.resize(img, (LLM_STILL_WIDTH, nh), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if ok:
                return base64.b64encode(buf.tobytes()).decode("utf-8")
    except Exception as e:
        logger.debug("cv2 re-encode failed for %s (%s); sending raw bytes", image_path, e)
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")

DETECT_PROMPT = "Look at this image and answer the questions."

# Multi-image timeline prompt. {n} is replaced with the number of frames sent
# (1 for the current frame only, 2 or 3 when prior frames are included).
# The model still returns the SAME JSON schema — the timeline context only
# changes what it looks at, not what we ask for.
TIMELINE_PROMPT = (
    "These {n} security camera frames were taken in sequence, seconds apart. "
    "Look at the LAST frame and answer the questions about it. "
    "Use the earlier frames only to understand motion and context "
    "(e.g. is a person walking toward the house, or standing still?). "
    "Do not describe the earlier frames — only answer about the final frame."
)

# HA front/back schemas — do not replace with person_at_car
# Structured flags are enforced by the inference API `format` field
# (Ollama /api/chat JSON Schema). Question text lives in each property
# `description` — never in the prompt.
FRONT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "postal_delivery",
        "postal_how",
        "dog_walked",
        "car_access",
        "enters_car",
        "exits_car",
        "car_outfit",
        "car_color",
        "car_make",
        "opens_box",
        "porch_access",
        "animal_detected",
        "animal_type",
    ],
    "properties": {
        "postal_delivery": {"type": "boolean", "description": 'Is a postie, mailman, courier, or parcel delivery happening?'},
        "postal_how": {"type": "string", "enum": ["van", "bike", "on foot", "truck", "scooter", "car", "unknown", "none"], "description": 'When postal_delivery is true, how the delivery arrives as a short lowercase phrase (van, bike, on foot, truck, scooter, car). If unknown use unknown. If postal_delivery is false, none.'},
        "dog_walked": {"type": "boolean", "description": 'Is a person walking a dog visible? A dog alone is animal_detected only.'},
        "car_access": {"type": "boolean", "description": 'Is a car driving into or accessing the driveway/street area?'},
        "enters_car": {"type": "boolean", "description": 'Is a person getting into / entering a car (opening door and boarding)? Mutually exclusive with exits_car when clear.'},
        "exits_car": {"type": "boolean", "description": 'Is a person getting out / exiting a car? Mutually exclusive with enters_car when clear.'},
        "car_outfit": {"type": "string", "maxLength": 24, "description": 'When enters_car or exits_car is true, person outfit in at most TWO lowercase words. Else none.'},
        "car_color": {"type": "string", "maxLength": 16, "description": 'When car_access or enters_car or exits_car is true, vehicle colour as ONE short lowercase word. Else none.'},
        "car_make": {"type": "string", "maxLength": 16, "description": 'When car_access or enters_car or exits_car is true, vehicle make as ONE short lowercase word if reasonably identifiable. Else none or unknown.'},
        "opens_box": {"type": "boolean", "description": 'Is a person actively opening a package, parcel, cardboard box, delivery box, or letterbox/mailbox? Not merely carrying a parcel.'},
        "porch_access": {"type": "boolean", "description": 'Is a person or visitor walking onto or accessing the front porch/entrance?'},
        "animal_detected": {"type": "boolean", "description": 'Is any animal visible (dog, cat, bird, wildlife), with or without a person?'},
        "animal_type": {"type": "string", "enum": ["dog", "cat", "bird", "wildlife", "other", "none"], "description": 'Short lowercase: dog, cat, bird, wildlife, other, or none. Must be none if animal_detected is false.'},
    },
}
BACK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "dog_walked",
        "approaching_house",
        "leaving_house",
        "weapon_detected",
        "clothes_drying",
        "animal_detected",
        "animal_type",
    ],
    "properties": {
        "dog_walked": {"type": "boolean", "description": 'Is a person walking a dog visible in the yard/back area? A dog alone is animal_detected only.'},
        "approaching_house": {"type": "boolean", "description": 'Is a person approaching the house (walking toward the house/door/porch, or arriving onto the property)? Mutually exclusive with leaving_house when direction is clear.'},
        "leaving_house": {"type": "boolean", "description": 'Is a person leaving the house (walking away toward the gate/street/off property)?'},
        "weapon_detected": {"type": "boolean", "description": 'Is a person holding or brandishing any weapon (knife, gun, bat, object)?'},
        "clothes_drying": {"type": "boolean", "description": 'Is a person hanging, pegging, or collecting clothes/laundry on a clothesline or drying rack?'},
        "animal_detected": {"type": "boolean", "description": 'Is any animal visible (dog, cat, bird, wildlife), with or without a person?'},
        "animal_type": {"type": "string", "enum": ["dog", "cat", "bird", "wildlife", "other", "none"], "description": 'Short lowercase: dog, cat, bird, wildlife, other, or none. Must be none if animal_detected is false.'},
    },
}
LLM_SCHEMA = FRONT_SCHEMA
FRONT_FLAG_KEYS = tuple(FRONT_SCHEMA["required"])
BACK_FLAG_KEYS = tuple(BACK_SCHEMA["required"])
LLM_ACTIVITY_KEYS = tuple(dict.fromkeys(FRONT_FLAG_KEYS + BACK_FLAG_KEYS))
LLM_ALL_FLAG_KEYS = set(LLM_ACTIVITY_KEYS)
LLM_TRIGGER_LABELS = ("person", "dog", "cat", "bird", "face", "body")
YOLO_PRESENCE_KEYS = ("person", "dog", "car", "cat", "bird", "face", "body")
LLM_META_KEYS = (
    "fast_pass", "_yolo", "description", "_llm", "_llm_skip",
    "_llm_model", "_llm_ms", "_llm_raw", "_centres", "_timeline_images",
)
FRONT_MAX_TOKENS = 220
BACK_MAX_TOKENS = 160

LLM_RAM_FLOOR_LOADED_GB = 0.8


# Camera kinds collapse onto the pipeline's two schema axes. `dog` is the
# historical label for the back/yard camera, accepted as an alias so a config
# can read naturally; anything unrecognised (including `other`) is front.
CAMERA_KIND_ALIASES = {
    "front": "front",
    "front_door": "front",
    "back": "back",
    "dog": "back",
    "dog_cam": "back",
    "dogcam": "back",
    "other": "front",
}


def normalize_camera_kind(kind):
    """Configured `kind` -> pipeline schema axis ('front'/'back').

    Unknown/`other` kinds fall back to 'front', matching the old default when
    no filename matched the legacy heuristic.
    """
    return CAMERA_KIND_ALIASES.get(str(kind or "").strip().lower(), "front")


def _camera_source(cam):
    """Configured image dir: `dir` (preferred) or the `source` alias."""
    return str(cam.get("dir") or cam.get("source") or "").strip()


def _legacy_camera_kind(blob):
    """Pre-cameras heuristic: Hikvision folder names and LAN IPs.

    This is the fallback for a settings.json without a `cameras[]` block and
    for paths that match no configured camera. Kept deliberately tiny; new
    deployments should describe cameras in `cameras[]` instead of extending it.
    """
    if "webcam22" in blob or "10.0.0.22" in blob:
        return "back"
    if "webcam21" in blob or "10.0.0.21" in blob:
        return "front"
    return "front"


def camera_kind(path):
    """front/back schema axis for an image path.

    Configured `cameras[]` wins: a path containing a camera's `dir`/`source`
    (or its id/label) returns that camera's normalised `kind`. When `cameras[]`
    is absent — or nothing matches — `_legacy_camera_kind` reproduces the
    pre-abstraction Webcam21/22 + 10.0.0.21/22 heuristic so existing boxes and
    fixtures keep working unchanged.
    """
    blob = (path or "").replace("\\", "/").lower()
    for cam in CAMERAS:
        if not isinstance(cam, dict):
            continue
        for needle in (_camera_source(cam), cam.get("id"), cam.get("label")):
            if needle and str(needle).replace("\\", "/").lower() in blob:
                return normalize_camera_kind(cam.get("kind"))
    return _legacy_camera_kind(blob)


def schema_for_kind(kind):
    """FRONT_SCHEMA or BACK_SCHEMA for a kind ('front'/'back') or image path."""
    if kind not in ("front", "back"):
        kind = camera_kind(kind)
    return BACK_SCHEMA if kind == "back" else FRONT_SCHEMA


def collect_timeline_images(image_dir, current_img, max_age_minutes=10.0, max_images=3):
    """Return prior frames from the same camera taken within max_age_minutes
    before current_img, oldest-first, capped to max_images total entries.

    The list always ends with current_img itself, so the caller can prepend
    it to the vision message directly. Returns [current_img] when no prior
    frames exist (1 image, single-frame analysis).
    """
    cur_path = os.path.join(image_dir, current_img)
    try:
        cur_mtime = os.path.getmtime(cur_path)
    except OSError:
        return [cur_path]
    cutoff = cur_mtime - (max_age_minutes * 60.0)
    prior = []
    try:
        for f in os.listdir(image_dir):
            if not f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
                continue
            if f == current_img:
                continue
            p = os.path.join(image_dir, f)
            try:
                t = os.path.getmtime(p)
            except OSError:
                continue
            if cutoff < t < cur_mtime:
                prior.append((t, p))
    except OSError:
        return [cur_path]
    prior.sort(key=lambda x: x[0])  # oldest first
    # Keep the most recent `max_images - 1` priors + current = max_images total
    keep = prior[-(max_images - 1):] if max_images > 1 else []
    return [p for _, p in keep] + [cur_path]


def schema_flag_default(schema, key):
    """Default for a missing HA schema field: False, 'none', or ''."""
    prop = (schema.get("properties") or {}).get(key) or {}
    if prop.get("type") == "boolean":
        return False
    enum = prop.get("enum") or []
    if "none" in enum:
        return "none"
    return ""


def _caption_inactive(v):
    if v is None:
        return True
    s = str(v).strip().lower()
    return (not s) or s in ("none", "false", "unknown")


def entry_caption(a):
    """One-line caption. Prefer description; else at most two HA facts.

    Mirrors index.html entryCaption so notify and the gallery say the same thing.
    """
    if not isinstance(a, dict):
        return ""
    desc = a.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    facts = []

    def push(s):
        if s and len(facts) < 2:
            facts.append(s)

    if a.get("postal_delivery") is True:
        how = a.get("postal_how")
        how = how.strip() if isinstance(how, str) else ""
        if _caption_inactive(how):
            push("Postal delivery")
        elif how.lower().startswith(("on ", "by ")):
            push("Postal delivery " + how.lower())
        else:
            push("Postal delivery by " + how.lower())
    if a.get("weapon_detected") is True:
        push("Weapon detected")
    if a.get("dog_walked") is True:
        push("Person walking a dog")
    if a.get("porch_access") is True:
        push("Someone at the porch")
    if a.get("animal_detected") is True and a.get("dog_walked") is not True:
        t = a.get("animal_type")
        t = t.strip() if isinstance(t, str) else ""
        if _caption_inactive(t):
            push("Animal in the yard")
        else:
            push(t[:1].upper() + t[1:].lower() + " in the yard")
    if a.get("clothes_drying") is True:
        push("Clothes on the line")
    if a.get("car_access") is True:
        push("Car in the driveway")
    if a.get("opens_box") is True:
        push("Someone opening a package")
    if a.get("enters_car") is True:
        push("Someone getting into a car")
    if a.get("exits_car") is True:
        push("Someone getting out of a car")
    if a.get("approaching_house") is True:
        push("Someone approaching the house")
    if a.get("leaving_house") is True:
        push("Someone leaving the house")
    return ". ".join(facts)


def get_llm_schema():
    """Read-only export of the live prompt + HA schemas for the UI viewer."""
    import scans
    return {
        "prompt": DETECT_PROMPT,
        "schemas": {
            "front_door": FRONT_SCHEMA,
            "dog_cam": BACK_SCHEMA,
        },
        "scans": [
            {"id": s["id"], "cameras": list(s["cameras"]),
             "need_any": list(s.get("need_any") or []),
             "need_all": list(s.get("need_all") or []),
             "schema": s["schema"]}
            for s in scans.SCANS
        ],
        "max_scans_per_image": scans.MAX_SCANS_PER_IMAGE,
        "dropped_flags": list(scans.DROPPED_FLAGS),
        "note": "Live path is YOLO-gated individual scans (not one giant schema). "
                "front_door/dog_cam remain the HA key lists. Dropped flags are "
                "not asked (systematic false positives).",
    }


def max_tokens_for_kind(kind):
    return BACK_MAX_TOKENS if kind == "back" else FRONT_MAX_TOKENS


_FNAME_TS = re.compile(r"_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\d{3}_")


def configured_tz_name():
    """Camera/report timezone: WEBCAM_TZ env > settings.json `timezone`
    > "Australia/Sydney" (delegates to api_server.resolve_timezone so the
    retaining sweep and the API agree on one clock)."""
    settings_val = _s.get("timezone") if isinstance(_s, dict) else None
    try:
        from api_server import resolve_timezone
        return resolve_timezone(os.getenv("WEBCAM_TZ"), settings_val)
    except Exception:
        for v in (os.getenv("WEBCAM_TZ"), settings_val):
            if isinstance(v, str) and v.strip():
                return v.strip()
        return "Australia/Sydney"


def filename_datetime(name, tz_name="Australia/Sydney", fold=0):
    """Parse the Hikvision filename clock into an aware datetime.

    Ambiguous wall-clock times occur on DST fall-back, when the local clock
    repeats an hour. `fold` picks the occurrence: 0 (default) = first pass,
    i.e. the earlier UTC instant (the pre-transition, larger UTC offset);
    1 = second pass, the later UTC instant. Unambiguous times and
    non-DST zones ignore `fold`. Returns None when the name has no embedded
    timestamp or the fields do not form a real date."""
    m = _FNAME_TS.search(str(name)) if name else None
    if not m:
        return None
    y, mo, d, h, mi, s = map(int, m.groups())
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = None
    try:
        return datetime(y, mo, d, h, mi, s, tzinfo=tz, fold=fold)
    except ValueError:
        return None


def filename_epoch(name, tz_name="Australia/Sydney", fold=0):
    """Filename clock as a UTC epoch float, or None if unparseable."""
    dt = filename_datetime(name, tz_name, fold)
    return None if dt is None else dt.timestamp()


def filename_within_days(name, days, now=None, tz_name="Australia/Sydney", fold=0):
    """True if the Hikvision filename clock is within the last `days` days."""
    try:
        days = int(days)
    except (TypeError, ValueError):
        return False
    if days < 1 or not name:
        return False
    dt = filename_datetime(name, tz_name, fold)
    if dt is None:
        return False
    if dt.tzinfo is not None:
        now = now or datetime.now(dt.tzinfo)
        if now.tzinfo is None:
            now = now.replace(tzinfo=dt.tzinfo)
    else:
        now = now or datetime.now()
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
    try:
        return timedelta(0) <= (now - dt) <= timedelta(days=days)
    except TypeError:
        return False


def rec_is_urgent_detection(rec):
    """Person/dog/cat/bird (not car-only). Used to pick a rescan set."""
    rec = rec if isinstance(rec, dict) else {}
    if llm_should_trigger(rec):
        return True
    yolo = rec.get("_yolo") or []
    return any(
        lbl in LLM_TRIGGER_LABELS and lbl not in GATE_IGNORE_LABELS
        for lbl in yolo
    )


def llm_should_trigger(fp_results):
    """LLM on person/dog/animal hits. Car-only does not trigger."""
    return any(k in LLM_TRIGGER_LABELS and k not in GATE_IGNORE_LABELS and v is True
               for k, v in (fp_results or {}).items())


def detector_labels(fp_results):
    return {k: v for k, v in (fp_results or {}).items()
            if k not in LLM_META_KEYS and k not in LLM_ALL_FLAG_KEYS}


def detector_true_labels(fp_results):
    """Sorted YOLO/detector keys that are True. Never HA flag names."""
    rec = fp_results or {}
    return sorted(k for k in YOLO_PRESENCE_KEYS if rec.get(k) is True)


def labels_and_centres(fp_results):
    """True YOLO labels plus any `_centres` map from fast_pass_yolo."""
    if isinstance(fp_results, (list, tuple, set)):
        return sorted({x for x in fp_results if x}), {}
    rec = fp_results or {}
    centres = rec.get("_centres") if isinstance(rec.get("_centres"), dict) else {}
    return detector_true_labels(rec), centres


def scan_skip_ids(kind):
    try:
        import zones
        return zones.gated_scan_ids(kind, IGNORE_REGIONS)
    except Exception:
        return set()


def scan_geometry_flags(kind, centres):
    try:
        import zones
        return zones.scan_gate_flags(kind, centres, IGNORE_REGIONS)
    except Exception:
        return {}


def is_llm_verified(rec):
    """True only after a successful merge. See catalog.kind == verified."""
    import catalog
    return catalog.is_llm_verified(rec)


def is_awaiting_backfill(rec):
    """Negatives and car-only no_trigger. See catalog.kind."""
    import catalog
    return catalog.is_awaiting_backfill(rec)


def in_backfill_pool(rec):
    """Idle deep_backfill candidates. See catalog.in_backfill_pool."""
    import catalog
    return catalog.in_backfill_pool(rec, GATE_IGNORE_LABELS)


def ollama_model_loaded(tag):
    """True if Ollama already has this local model resident (/api/ps)."""
    if not tag or model_is_cloud(tag):
        return False
    try:
        r = requests.get(f"{OLLAMA_URL}/api/ps", timeout=2)
        if not r.ok:
            return False
        want = tag.strip()
        for m in (r.json() or {}).get("models", []) or []:
            name = (m.get("name") or m.get("model") or "").strip()
            if name == want or name.startswith(want):
                return True
    except Exception:
        return False
    return False


def local_mem_threshold():
    """If e2b is already loaded, only require a small decode floor.
    If it is not loaded, require min_mem_for_local_gb so we skip rather than OOM."""
    for tag in (MODEL_PRIMARY, MODEL_FALLBACK):
        if isinstance(tag, str) and tag.strip() and ollama_model_loaded(tag):
            return LLM_RAM_FLOOR_LOADED_GB
    return MIN_MEM_FOR_LOCAL_GB

# Home Assistant vision schemas for structured e2b deep-pass analysis
# (enforced via Ollama API `format` parameter, not in the prompt)
# NOTE: FRONT_SCHEMA / BACK_SCHEMA are defined above (with `required`).
# This comment block documents the live schema used by scans.py.
def analyze_image_openrouter(image_path, api_key, schema=None, num_predict=None):
    """Cloud fallback: one JSON schema (full HA or a single scan)."""
    kind = camera_kind(image_path)
    schema = schema or schema_for_kind(kind)
    npred = int(num_predict or max_tokens_for_kind(kind))
    base64_image = encode_image(image_path)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    payload = {
        "model": MODEL_CLOUD,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": DETECT_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    },
                ],
            }
        ],
        "max_tokens": npred,
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": f"{kind}_ha_flags",
                "strict": True,
                "schema": schema,
            },
        },
    }

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers, data=json.dumps(payload), timeout=60)
        if response.status_code != 200:
            return None
        content = response.json()["choices"][0]["message"]["content"].strip()
        return json.loads(content)
    except Exception:
        return None

# --- Inference audit trail (read by api_server for the UI) ---
INFERENCE_LOG = os.path.join(BASE_DIR, "inference_log.json")
INFERENCE_STATUS = os.path.join(BASE_DIR, "inference_status.json")

# Serializes the small audit-trail files so concurrent deep passes (and the
# live API reader) never see a half-written or interleaved JSON file.
_IO_LOCK = threading.Lock()


def _atomic_write_json(path, data, indent=None, mode=0o644):
    """Write JSON to `path` via temp-file + os.replace so a reader never
    observes a partially written file, and a crash mid-write can't truncate
    the real one. Caller holds _IO_LOCK when ordering vs other writers matters.

    `mode` is the final perms of the destination file. Default 0o644 so the
    web server (nginx, running as www-data) can read catalog files the
    pipeline writes as its own user — a 0o600 analysis.json 403s the whole
    AI layer of the gallery. Callers that hold secrets (integrations.json)
    should pass mode=0o600.
    """
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# Catalog paths whose last load recovered only a *prefix* of a truncated file
# (see load_json_file). Rows past the cut are gone, so the frames they
# described look like unanalysed backlog and stop being persistable — the age
# and disk-budget passes then delete real LLM-verified evidence (#29). While a
# catalog is partial, retention exempts uncatalogued frames. Module-level (not
# a return value) so load_json_file keeps a single return type.
_PARTIAL_CATALOGS = set()


def catalog_is_partial(path=None):
    """True when a catalog is a partial recovery — *path* if given, else any."""
    return bool(_PARTIAL_CATALOGS) if path is None else path in _PARTIAL_CATALOGS


def _recover_truncated_json(text, default):
    """Best-effort repair of a truncated JSON object/array (typical ENOSPC
    mid-write). Drops a trailing incomplete key/value and closes the root."""
    if not text or not text.strip():
        return default
    text = text.strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass

    # Prefer truncating before an incomplete trailing key (`\n  "foo...`).
    idx = text.rfind("\n  \"")
    while idx > 0:
        candidate = text[:idx].rstrip().rstrip(",") + "\n}\n"
        try:
            obj = json.loads(candidate)
            if isinstance(obj, type(default)):
                return obj
        except (ValueError, TypeError):
            pass
        idx = text.rfind("\n  \"", 0, idx)

    # Walk back through closing braces near the tail (bounded).
    start = max(0, len(text) - 250_000)
    for i in range(len(text) - 1, start, -1):
        if text[i] != "}":
            continue
        chunk = text[: i + 1].rstrip().rstrip(",")
        for suffix in ("", "\n}", "\n]\n}", "]}"):
            try:
                obj = json.loads(chunk + suffix)
                if isinstance(obj, type(default)):
                    return obj
            except (ValueError, TypeError):
                continue
    return default


def load_json_file(path, default=None, catalog=False):
    """Load JSON from disk. On corruption (e.g. truncated mid-write), attempt
    recovery. A *catalog* (analysis.json / bursts.json) that was only
    partially recovered is never written back over the original: the tail past
    the cut still holds the only record that the frames it described were
    LLM-verified, and replacing the file with the recovered prefix made those
    frames uncatalogued — after which retention deleted real verified
    evidence (#29). The prefix is parked in a ``<path>.recovered`` sidecar, the
    original bytes are kept, and the path is marked partial so retention
    exempts uncatalogued frames until a full sweep rebuilds their rows.
    ``catalog=True`` is set by the image catalogs only, so a corrupt
    retention_log.json cannot arm the protection. Returns a fresh empty
    dict/list when the file is missing or unrecoverable — never raises for
    parse errors."""
    if default is None:
        default = {}
    empty = {} if isinstance(default, dict) else ([] if isinstance(default, list) else default)
    if not os.path.exists(path):
        return empty
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        print(f"Warning: could not read {path} ({e}); using empty default")
        return empty
    if not raw.strip():
        return empty
    if catalog:
        _PARTIAL_CATALOGS.discard(path)
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as e:
        print(f"Warning: {path} is corrupt ({e}); attempting recovery")
    recovered = _recover_truncated_json(raw, empty)
    # Sentinel: recovery failed when we got back the empty default object we
    # passed in *and* the file clearly had substantial content we couldn't parse.
    n = len(recovered) if hasattr(recovered, "__len__") else 0
    if n == 0 and len(raw) > 8 and recovered is empty:
        # Nothing at all came back, so the whole catalog is lost. Mark it
        # partial anyway: every row is missing, so every frame looks
        # unanalysed and the age/budget passes would delete the lot.
        if catalog:
            _PARTIAL_CATALOGS.add(path)
        print(f"Could not recover {path}; starting from empty"
              + ("" if catalog else " — uncatalogued frames are protected from retention"))
        return empty
    if catalog:
        _PARTIAL_CATALOGS.add(path)
    sidecar = f"{path}.recovered"
    try:
        _atomic_write_json(sidecar, recovered, indent=2)
    except OSError as we:
        print(f"Warning: could not write recovered sidecar {sidecar}: {we}")
    print(f"Recovered prefix of {path}: retained {n} entries; original left "
          f"intact, prefix saved to {sidecar}")
    return recovered


def set_inference_status(payload):
    """Live marker of the in-flight LLM call ({} when idle)."""
    try:
        with _IO_LOCK:
            _atomic_write_json(INFERENCE_STATUS, payload or {})
    except OSError as e:
        logger.warning("could not write inference status %s: %s", INFERENCE_STATUS, e)

def log_inference(image, model, started, duration, labels, ok, trigger, n_images=None):
    with _IO_LOCK:
        try:
            if os.path.exists(INFERENCE_LOG):
                with open(INFERENCE_LOG) as _lf:
                    log = json.load(_lf)
            else:
                log = []
        except (OSError, ValueError):
            log = []
        entry = {"image": image, "model": model, "trigger": trigger,
                 "started": started, "duration_s": round(duration, 1),
                 "labels": labels, "ok": ok}
        if n_images is not None:
            entry["n_images"] = n_images
        log.append(entry)
        try:
            _atomic_write_json(INFERENCE_LOG, log[-200:], indent=1)
        except OSError as e:
            logger.warning("could not write inference log %s: %s", INFERENCE_LOG, e)

def merge_llm_into_fastpass(fp_results, llm_result, skip_reason=None, model=None, duration_s=None, raw=None, schema=None):
    """Keep YOLO/detector flags; attach structured LLM flags or a skip reason.

    Schema keys are merged onto the existing record. Detector person/dog/car
    (and other YOLO labels) are never overwritten by the LLM. When ``schema``
    is set, only its properties are copied and missing required fields are
    filled with schema_flag_default (False / 'none' / '').
    """
    rec = detector_labels(fp_results)
    if skip_reason or not isinstance(llm_result, dict):
        rec["fast_pass"] = "partial"
        rec["_llm_skip"] = skip_reason or "llm_failed"
        if model:
            rec["_llm_model"] = model
        if duration_s is not None:
            rec["_llm_ms"] = int(duration_s * 1000)
        import catalog
        return catalog.stamp(rec)
    flags = {}
    allowed = set((schema or {}).get("properties") or {}) if schema else None
    for k, v in llm_result.items():
        if k in LLM_META_KEYS or str(k).startswith("_"):
            continue
        if allowed is not None and k not in allowed:
            continue
        if k in YOLO_PRESENCE_KEYS:
            flags[k] = v  # keep under _llm only; do not overwrite YOLO
            continue
        rec[k] = v
        flags[k] = v
    if schema:
        for k in schema.get("required") or []:
            if k in YOLO_PRESENCE_KEYS:
                continue
            if k not in rec:
                rec[k] = schema_flag_default(schema, k)
            if k not in flags:
                flags[k] = rec[k]
    rec["_llm"] = flags
    if model:
        rec["_llm_model"] = model
    if duration_s is not None:
        rec["_llm_ms"] = int(duration_s * 1000)
    if raw is not None:
        rec["_llm_raw"] = raw
    if "_yolo" in llm_result:
        rec["_yolo"] = llm_result["_yolo"]
    elif "_yolo" not in rec:
        rec["_yolo"] = sorted(k for k, v in rec.items() if k in YOLO_PRESENCE_KEYS and v is True)
    import catalog
    return catalog.stamp(rec)


_catalog_flush_ts = 0.0


def flush_analysis(analysis_file, analysis_data, force=False):
    """Atomic catalog write. Debounced (~1s) so a busy sweep does not rewrite
    a multi-MB JSON on every frame; force=True at end of camera / process."""
    global _catalog_flush_ts
    now = time.time()
    if not force and (now - _catalog_flush_ts) < 1.0:
        return
    try:
        _atomic_write_json(analysis_file, analysis_data, indent=2)
        _catalog_flush_ts = now
    except OSError as e:
        print(f"Failed to write analysis.json (disk full?): {e}")


def persist_row(analysis_file, analysis_data, img, rec, *, existed):
    """Stamp schema 1, emit a live event, flush the catalog.

    Live no longer waits for the end-of-sweep write. image.new fires when
    the file is first catalogued; detector hits emit preliminary or
    new-detection from catalog.kind.
    """
    import catalog
    import pipeline_events
    rec = catalog.stamp(rec)
    analysis_data[img] = rec
    labels = catalog.detector_true_labels(rec)
    k = catalog.kind(rec)
    if not existed:
        pipeline_events.emit("image.new", file=img)
    if k == "verified" and labels:
        pipeline_events.emit("new-detection", file=img, labels=labels)
    elif k in ("preliminary", "no_trigger") and labels:
        pipeline_events.emit("detection.preliminary", file=img, labels=labels)
    flush_analysis(analysis_file, analysis_data, force=False)
    return rec


def run_deep_pass(image_path, img_name, can_run_chain, api_key, trigger, fp_labels=None, timeline_images=None):
    """YOLO-gated individual e2b scans (not one giant HA schema).

    Returns ``(result_or_none, n_calls)``. n_calls counts against
    max_deep_passes. Empty scans (e.g. backyard person, no dog) → (None, 0)
    so the caller can persist ``_llm_skip=no_scan`` without re-queueing.
    fp_labels=None means run the detector fresh. A dict from
    fast_pass_yolo (including ``_centres``) is also accepted.

    ``timeline_images`` is an optional list of prior frame paths (oldest
    first) to prepend to the vision message so the model sees motion context.
    """
    global LAST_DURATION_S
    import scans
    if RATE_LIMITED:
        return None, 0
    started = time.time()
    centres = {}
    if fp_labels is None:
        fp = fast_pass_dispatch(image_path)
        if fp is FAST_PASS_UNREADABLE:
            return None, 0
        fp = fp or {}
        fp_labels, centres = labels_and_centres(fp)
    elif isinstance(fp_labels, dict):
        fp_labels, centres = labels_and_centres(fp_labels)
    kind = camera_kind(image_path)
    skip = scan_skip_ids(kind)
    todo = scans.scans_for(kind, fp_labels, scans.MAX_SCANS_PER_IMAGE, skip=skip)
    seed = scans.yolo_animal_seed(fp_labels)
    seed.update(scan_geometry_flags(kind, centres))
    if not todo:
        LAST_DURATION_S = time.time() - started
        if seed:
            seed["_yolo"] = list(fp_labels or [])
            seed["_scans"] = ["yolo_animal"] if scans.yolo_animal_seed(fp_labels) else ["porch_gate"]
            if timeline_images:
                seed["_timeline_images"] = len(timeline_images)
            return seed, 0
        return None, 0

    combined = dict(seed)
    n = 0
    used = None
    for spec in todo:
        if RATE_LIMITED:
            break
        set_inference_status({"image": img_name, "model": MODEL_PRIMARY,
                              "trigger": trigger, "started": started,
                              "scan": spec["id"]})
        n += 1
        piece = None
        if can_run_chain:
            piece = analyze_image_with_schema(
                image_path, spec["schema"], spec.get("num_predict") or scans.SCAN_TOKENS,
                extra_images=timeline_images)
            if isinstance(piece, dict):
                used = LAST_MODEL_USED
        if not piece and ALLOW_CLOUD and api_key:
            used = MODEL_CLOUD
            piece = analyze_image_openrouter(
                image_path, api_key, schema=spec["schema"],
                num_predict=spec.get("num_predict") or scans.SCAN_TOKENS)
        if isinstance(piece, dict):
            combined.update(piece)

    set_inference_status(None)
    result = combined or None
    if isinstance(result, dict):
        result["_yolo"] = list(fp_labels or [])
        result["_scans"] = [s["id"] for s in todo[:n]]
        if timeline_images:
            result["_timeline_images"] = len(timeline_images)
    labels = list(fp_labels or [])
    LAST_DURATION_S = time.time() - started
    log_inference(img_name, used, started, LAST_DURATION_S,
                  labels, result is not None, trigger,
                  n_images=len(timeline_images) if timeline_images else 1)
    return result, n


def maybe_notify_urgent_frame(img, rec, image_path, *, trigger, prev=None):
    """Notify after persisting an urgent detector hit.

    Fires on trigger=="priority" when the image is new or detector True
    keys changed. Skips car-only no_trigger, idle backfill, and unchanged
    skip-partials. Caption is entry_caption after a successful merge;
    objects mode does not need one. A notifier failure never breaks the sweep.
    """
    if trigger != "priority":
        return False
    if not isinstance(rec, dict) or not llm_should_trigger(rec):
        return False
    labels = detector_true_labels(rec)
    if not labels:
        return False
    if isinstance(prev, dict) and detector_true_labels(prev) == labels:
        return False
    caption = entry_caption(rec) if is_llm_verified(rec) else ""
    try:
        from integrations import notify_image
        notify_image(img, labels, caption, image_path)
    except Exception as e:
        print(f"notify_image failed: {e}")
    return True


def ollama_available():
    """True if a local Ollama server is reachable. Generous timeout:
    when inference has all cores busy, /api/version can take seconds."""
    try:
        return requests.get(f"{OLLAMA_URL}/api/version", timeout=15).ok
    except requests.exceptions.RequestException:
        return False

# Rate-limit handling. The Ollama endpoint may be a cloud model (model tag
# ending ":cloud") which can throttle; we must NOT hammer it. Strategy:
#   - per call: retry a 429/503 a few times with EXPONENTIAL BACKOFF + jitter,
#     capped so we never hold the sweep's global lock for long;
#   - if still throttled, raise RateLimited so the sweep stops its remaining
#     deep passes (RATE_LIMITED flag) and resumes on the next sweep (~60s).
RATE_LIMITED = False          # set per-sweep when the LLM endpoint throttles us
LAST_MODEL_USED = None        # the chain model that actually served the last call
LAST_DURATION_S = None        # seconds for the last deep pass (ok or fail)
                              # (so logs/status show real cloud-vs-local, not a guess)
LLM_MAX_RETRIES = 4           # attempts before giving up a single call
LLM_BACKOFF_BASE = 2.0        # seconds; doubles each retry
LLM_BACKOFF_CAP = 30.0        # cap a single wait (sweep lock has a 1h timeout)


class RateLimited(Exception):
    """The LLM endpoint throttled us after exhausting backoff retries."""


def backoff_delay(attempt, base=LLM_BACKOFF_BASE, cap=LLM_BACKOFF_CAP):
    """Exponential backoff for retry `attempt` (0-based), capped. Pure."""
    return min(cap, base * (2 ** attempt))


def ollama_payload(payload):
    """Copy a chat payload and default keep_alive so activity holds the model."""
    out = dict(payload or {})
    out.setdefault("keep_alive", OLLAMA_KEEP_ALIVE)
    return out


def _ollama_chat(payload, timeout):
    """POST /api/chat with exponential-backoff retry on 429/503. Returns the
    response on success; raises RateLimited if still throttled after
    LLM_MAX_RETRIES. Other request errors propagate to the caller."""
    payload = ollama_payload(payload)
    for attempt in range(LLM_MAX_RETRIES):
        resp = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
        if resp.status_code in (429, 503):
            if attempt == LLM_MAX_RETRIES - 1:
                raise RateLimited(f"LLM throttled (HTTP {resp.status_code})")
            # honor Retry-After if present, else exponential backoff; ±20% jitter
            ra = resp.headers.get("Retry-After")
            wait = float(ra) if (ra and ra.isdigit()) else backoff_delay(attempt)
            time.sleep(min(LLM_BACKOFF_CAP, wait) * (0.8 + 0.4 * random.random()))
            continue
        resp.raise_for_status()
        return resp
    raise RateLimited("LLM throttled")  # defensive; loop above normally returns/raises


def model_chain(primary, fallback):
    """Ordered, de-duplicated, non-empty [primary, fallback] models to try. Pure."""
    seen, out = set(), []
    for m in (primary, fallback):
        m = m.strip() if isinstance(m, str) else ""
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def model_is_cloud(tag):
    """True for an Ollama cloud model (':cloud' suffix): it runs on Ollama's
    servers and needs NO local RAM. Pure."""
    return isinstance(tag, str) and tag.strip().endswith(":cloud")


def runnable_chain(primary, fallback, free_gb, min_local_gb, allow_cloud):
    """model_chain() filtered to the models THIS host can actually serve:
    cloud models only when allow_cloud (an Ollama ':cloud' tag ships frames to
    Ollama's servers, so the `allow_cloud` kill switch must gate it exactly
    like OpenRouter); a local model only when free_gb >= min_local_gb.
    Pure. Without the cloud branch, a low-RAM box silently does zero deep
    passes even though its configured cloud primary needs no local memory."""
    return [m for m in model_chain(primary, fallback)
            if (allow_cloud if model_is_cloud(m) else free_gb >= min_local_gb)]


def concurrency_workers(requested, n_targets):
    """Clamp the configured backfill concurrency to a sane worker count:
    at least 1, never more than the number of targets. Pure."""
    try:
        req = int(requested)
    except (TypeError, ValueError):
        req = 1
    return max(1, min(req, n_targets)) if n_targets > 0 else 0


def parse_chat_chunk(line):
    """Parse one NDJSON line of an Ollama streaming chat response into
    (text_delta, done). Pure; a malformed line yields ("", False)."""
    try:
        d = json.loads(line)
    except (ValueError, TypeError):
        return ("", False)
    text = (d.get("message") or {}).get("content", "") or ""
    return (text, bool(d.get("done")))


def _ollama_chat_stream(payload, on_delta, timeout):
    """Stream /api/chat (stream:true), accumulating the content. Calls
    on_delta(delta, accumulated) per chunk; returns the full text. Shares the
    429/503 backoff + RateLimited semantics of _ollama_chat."""
    payload = ollama_payload({**payload, "stream": True})
    for attempt in range(LLM_MAX_RETRIES):
        resp = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout, stream=True)
        if resp.status_code in (429, 503):
            if attempt == LLM_MAX_RETRIES - 1:
                raise RateLimited(f"LLM throttled (HTTP {resp.status_code})")
            ra = resp.headers.get("Retry-After")
            wait = float(ra) if (ra and ra.isdigit()) else backoff_delay(attempt)
            time.sleep(min(LLM_BACKOFF_CAP, wait) * (0.8 + 0.4 * random.random()))
            continue
        resp.raise_for_status()
        full = ""
        for line in resp.iter_lines():
            if not line:
                continue
            text, done = parse_chat_chunk(line.decode("utf-8") if isinstance(line, bytes) else line)
            if text:
                full += text
                if on_delta:
                    on_delta(text, full)
            if done:
                break
        return full
    raise RateLimited("LLM throttled")  # defensive


def analyze_image_with_schema(image_path, schema, num_predict, extra_images=None):
    """One Ollama JSON-schema call. Used by sequential priority scans.

    ``extra_images`` is an optional list of prior frame paths (oldest first)
    taken within a few minutes of ``image_path``. When provided, the vision
    message includes them so the model sees motion/context across time and
    the prompt asks for a short timeline instead of a single snapshot.
    """
    global RATE_LIMITED, LAST_MODEL_USED
    images = [encode_image(image_path)]
    if extra_images:
        images = [encode_image(p) for p in extra_images] + images
    if len(images) > 1:
        prompt = TIMELINE_PROMPT.format(n=len(images))
    else:
        prompt = DETECT_PROMPT
    base = {
        "messages": [{
            "role": "user",
            "content": prompt,
            "images": images,
        }],
        "stream": False,
        "format": schema,
        "think": False,
        "options": {"num_predict": int(num_predict), "temperature": 0},
    }
    rate_limited_all, tried = True, 0
    for model in runnable_chain(MODEL_PRIMARY, MODEL_FALLBACK, get_free_mem_gb(), local_mem_threshold(), ALLOW_CLOUD):
        tried += 1
        try:
            response = _ollama_chat({**base, "model": model}, timeout=600)
            result = json.loads(response.json()["message"]["content"])
            if isinstance(result, dict):
                LAST_MODEL_USED = model
                return result
            return None
        except RateLimited:
            continue
        except requests.exceptions.ConnectionError:
            return None
        except Exception as e:
            rate_limited_all = False
            print(f"Inference failed on {model}: {e}")
            continue
    if tried and rate_limited_all:
        RATE_LIMITED = True
        print("LLM rate-limited across the model chain; backing off for this sweep")
    return None


def analyze_image_local(image_path):
    """Legacy one-shot full front/back schema (tests + fallback)."""
    kind = camera_kind(image_path)
    return analyze_image_with_schema(
        image_path, schema_for_kind(kind), max_tokens_for_kind(kind))

BURST_PROMPT = "These webcam frames were taken in sequence. Describe what happens across them - any people, animals, birds, vehicles, or notable changes in the scene (lighting, objects moving). Don't assume a person is the subject. If nothing meaningfully changes, say so in one sentence."

def analyze_burst_local(image_paths, on_progress=None):
    """Burst (context) summary via the Ollama server (multi-image message),
    STREAMED. on_progress(accumulated_text) fires as the caption builds so the
    UI can show it live ("AI is looking at this…"). Returns the full text."""
    global RATE_LIMITED, LAST_MODEL_USED
    base = {
        "messages": [{
            "role": "user",
            "content": BURST_PROMPT,
            "images": [encode_image(p) for p in image_paths],
        }],
    }
    cb = (lambda delta, acc: on_progress(acc)) if on_progress else None
    rate_limited_all, tried = True, 0
    for model in runnable_chain(MODEL_PRIMARY, MODEL_FALLBACK, get_free_mem_gb(), local_mem_threshold(), ALLOW_CLOUD):
        tried += 1
        try:
            full = _ollama_chat_stream({**base, "model": model}, cb, timeout=900)
            if full:
                LAST_MODEL_USED = model
                return full.strip()
            return None
        except RateLimited:
            continue
        except Exception as e:
            rate_limited_all = False
            print(f"Burst analysis failed on {model}: {e}")
            continue
    if tried and rate_limited_all:
        RATE_LIMITED = True
        print("LLM rate-limited on burst across the model chain; backing off for this sweep")
    return None

def analyze_burst_openrouter(image_paths, api_key):
    """Analyze a sequence of images for a textual description."""
    content_list = [{ "type": "text", "text": BURST_PROMPT }]
    
    for path in image_paths:
        base64_img = encode_image(path)
        content_list.append({
            "type": "image_url",
            "image_url": { "url": f"data:image/jpeg;base64,{base64_img}" }
        })

    payload = {
        "model": MODEL_CLOUD,
        "messages": [{ "role": "user", "content": content_list }]
    }
    
    try:
        headers = { "Authorization": f"Bearer {api_key}", "Content-Type": "application/json" }
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, data=json.dumps(payload), timeout=90)
        return response.json()['choices'][0]['message']['content'].strip()
    except Exception:
        return "Failed to analyze sequence."

def get_free_mem_gb():
    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
            free = [line for line in lines if line.startswith('MemAvailable')][0]
            return int(free.split()[1]) / (1024 * 1024)
    except Exception:
        return 0

THUMB_WIDTH = 320

def has_detection(entry):
    return any(v is True for k, v in entry.items() if k != 'fast_pass')


def retention_is_keep_detection(entry):
    """True if the disk-budget pass should treat this frame as a detection.

    Gate-ignore labels (default: car) do not count — parked-car-only frames
    evict like negatives when a camera is over max_dir_gb.
    """
    if not isinstance(entry, dict):
        return False
    ignore = set(GATE_IGNORE_LABELS)
    ignore.add("fast_pass")
    return any(v is True and k not in ignore for k, v in entry.items())


def retention_is_persistable(entry):
    """LLM-verified timeline visit. May outlive max_age_days (persist cap)."""
    import catalog
    return catalog.is_timeline_persistable(entry, GATE_IGNORE_LABELS)


def persist_budget_bytes():
    """Byte ceiling for age-expired LLM timeline frames (persist_budget_pct)."""
    try:
        pct = float(PERSIST_BUDGET_PCT)
    except (TypeError, ValueError):
        pct = 20.0
    pct = max(0.0, min(100.0, pct))
    return MAX_DIR_GB * 1024 ** 3 * (pct / 100.0)


def _thumb_sizes(image_dir):
    """filename -> size for files in <image_dir>/thumbs/. Missing dir -> {}."""
    thumbs = {}
    try:
        with os.scandir(os.path.join(image_dir, "thumbs")) as it:
            for e in it:
                if not e.is_file():
                    continue
                try:
                    thumbs[e.name] = e.stat().st_size
                except OSError:
                    continue
    except OSError as e:
        logger.debug("could not scan thumbs in %s: %s", image_dir, e)
    return thumbs


def dir_image_usage(image_dir):
    """Newest image mtime, image count, bytes charged to max_dir_gb.

    Charged bytes are each top-level image plus its matching thumbnail.
    JSON/HTML/guide copies are not counted.
    """
    thumbs = _thumb_sizes(image_dir)
    newest, count, total = 0.0, 0, 0
    try:
        with os.scandir(image_dir) as it:
            for e in it:
                if not (e.is_file() and e.name.lower().endswith(
                        ('.jpg', '.jpeg', '.png', '.gif'))):
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                count += 1
                total += st.st_size + thumbs.get(e.name, 0)
                newest = max(newest, st.st_mtime)
    except OSError as e:
        logger.debug("could not scan image dir %s: %s", image_dir, e)
    return newest, count, total

RETENTION_LOG = os.path.join(BASE_DIR, "retention_log.json")

def _log_retention(image_dir, count, bytes_freed):
    """Audit trail of deletions so vanishing images are explainable in-UI."""
    log = load_json_file(RETENTION_LOG, [])
    if not isinstance(log, list):
        log = []
    log.append({"ts": time.time(), "dir": os.path.basename(image_dir.rstrip("/")),
                "count": count, "bytes_freed": bytes_freed})
    try:
        _atomic_write_json(RETENTION_LOG, log[-100:], indent=1)
    except OSError as e:
        logger.warning("could not write retention log %s: %s", RETENTION_LOG, e)

ALERT_STATE = os.path.join(BASE_DIR, "alert_state.json")
# Belt-and-braces Slack rate limiting for health alerts (esp. disk/space spam)
ALERT_BASE_COOLDOWN_S = 1800          # 30 min base; doubles with each re-fire of same key
ALERT_MAX_COOLDOWN_S = 12 * 3600      # 12 h ceiling
RECOVERY_COOLDOWN_S = 3600            # 1 h between recovery notices for the same key
MAX_ALERTS_PER_WINDOW = 4             # hard global cap on alert/recovery sends per window
ALERT_WINDOW_S = 3600                 # the window for the global max
IDENTICAL_UPDATE_S = 600               # 10 min between counter bumps on same Slack message


def alert_with_count(base_msg, count):
    """Append _(×N) suffix when count > 1. Pure."""
    if count <= 1:
        return base_msg
    return f"{base_msg}  _(×{count})"


def alert_base_text(text):
    """Strip optional _(×N) suffix for identical-text comparison. Pure."""
    if not text:
        return ""
    import re
    return re.sub(r"  _\(×\d+\)$", "", text).rstrip()


def alert_cooldown(fire_count, identical=False, has_slack_ts=False):
    """Seconds to wait before next send/update for this key. Pure.

    Identical text with an existing Slack message uses a short fixed interval
    so the counter can tally without full exponential backoff. Brand-new or
    different text uses exponential backoff (base × 2^n, capped).
    """
    if identical and has_slack_ts:
        return IDENTICAL_UPDATE_S
    return min(ALERT_BASE_COOLDOWN_S * (2 ** min(int(fire_count), 5)), ALERT_MAX_COOLDOWN_S)


def alert_next_count(fire_count, identical):
    """Next fire_count value. Identical → increment; different → reset to 1. Pure."""
    return (int(fire_count) + 1) if identical else 1


def _scan_dir(image_dir):
    """Newest image mtime and bytes charged to max_dir_gb (image + thumb)."""
    newest, _count, total = dir_image_usage(image_dir)
    return newest, total

def _recovery_text(key):
    if key == "llm_down":
        return "✅ Inference restored — Ollama reachable again."
    if key == "infer_fail":
        return "✅ Inference failures cleared."
    if key.startswith("cam_"):
        return f"✅ {key[4:]}: receiving frames again."
    if key.startswith("disk_"):
        return f"✅ {key[5:]}: storage back under budget."
    return f"✅ Recovered: {key}"


def camera_offline_hours(settings=None, path=None):
    """Hours of silence before a camera is offline. Missing/invalid → 24."""
    try:
        if settings is None:
            with open(path or _settings_path) as f:
                settings = json.load(f)
        hours = settings.get("camera_offline_hours", CAMERA_OFFLINE_HOURS_DEFAULT)
        return float(hours)
    except (OSError, ValueError, TypeError, AttributeError):
        return float(CAMERA_OFFLINE_HOURS_DEFAULT)


def run_health_checks(watch_dirs, api_key):
    """Evaluate pipeline / camera / storage health and push DEBOUNCED Slack
    alerts (plus recovery notices) through the integrations layer. Runs once
    per sweep; fully guarded so it can never break the pipeline."""
    now = time.time()
    offline_s = camera_offline_hours() * 3600

    alerts = {}
    # Inference capability. Only a genuinely DOWN Ollama (server unreachable)
    # with no cloud fallback is an outage. Don't alert on `can_run_chain`
    # being briefly false — that includes the free-memory gate, which flaps
    # sweep-to-sweep and recovers on its own (false alarms otherwise).
    if DEEP_PASSES_ENABLED and not ollama_available() and not (ALLOW_CLOUD and api_key):
        alerts["llm_down"] = ("⚠️ *Inference unavailable* — local Ollama is unreachable "
                              "and no cloud fallback is configured. New images won't be analysed.")

    budget = MAX_DIR_GB * 1024 ** 3
    for d in watch_dirs:
        if not os.path.exists(d):
            continue
        name = os.path.basename(d.rstrip('/'))
        newest, total = _scan_dir(d)
        if newest == 0 or now - newest > offline_s:
            ago = "no frames yet" if newest == 0 else f"{int((now - newest) / 3600)}h"
            alerts[f"cam_{name}"] = f"⚠️ *{name}*: no new frames in {ago} — camera offline?"
        if budget and total > 0.9 * budget:
            alerts[f"disk_{name}"] = (f"⚠️ *{name}*: storage at {int(100 * total / budget)}% "
                                      f"of the {MAX_DIR_GB:g}GB budget — retention is deleting images.")

    try:
        with open(INFERENCE_LOG) as _lf:
            log = json.load(_lf)
    except (OSError, ValueError):
        log = []
    fails = sum(1 for e in log if now - e.get("started", 0) <= 3600 and not e.get("ok"))
    if fails >= 3:
        alerts["infer_fail"] = f"⚠️ *{fails} inference failures* in the last hour."

    state = load_json_file(ALERT_STATE, {})
    if not isinstance(state, dict):
        state = {}

    try:
        from integrations import notify_alert
    except Exception as e:
        logger.warning("integrations.notify_alert unavailable (%s); alerts will not be sent", e)
        notify_alert = None

    def send(msg, update_ts=None):
        """Send (or update) a health alert. Returns the Slack ts when available."""
        if not notify_alert:
            return None
        try:
            result = notify_alert(msg, update_ts=update_ts)
            # notify_alert now returns (ok, ts)
            if isinstance(result, tuple) and len(result) == 2:
                ok, ts = result
                return ts if ok else None
            return None
        except Exception as e:
            print(f"Alert send failed: {e}")
            return None

    def _recent_sends(state):
        meta = state.get("_meta", {})
        recent = [t for t in meta.get("recent_sends", []) if now - t < ALERT_WINDOW_S]
        return recent

    def _record_send(state):
        meta = state.setdefault("_meta", {})
        recent = [t for t in meta.get("recent_sends", []) if now - t < ALERT_WINDOW_S]
        recent.append(now)
        meta["recent_sends"] = recent[-MAX_ALERTS_PER_WINDOW:]
        state["_meta"] = meta

    def _can_send(state):
        return len(_recent_sends(state)) < MAX_ALERTS_PER_WINDOW

    # Fire / update alerts — prefer editing the previous Slack message.
    # Identical text → counter tally via chat.update; different text → full backoff.
    for key, msg in alerts.items():
        prev = state.get(key, {})
        fire_count = int(prev.get("fire_count", 0))
        prev_ts = prev.get("slack_ts")
        prev_text = prev.get("last_text") or ""
        identical = (alert_base_text(msg) == alert_base_text(prev_text)) and bool(prev_text)

        cooldown = alert_cooldown(fire_count, identical=identical, has_slack_ts=bool(prev_ts))
        # Identical text with an existing Slack message must always respect the
        # short update interval — even if active briefly flipped false (rapid flap).
        if identical and prev_ts:
            due = (now - prev.get("last_fired", 0) > cooldown)
        else:
            due = (not prev.get("active")) or (now - prev.get("last_fired", 0) > cooldown)

        if due and _can_send(state):
            count = alert_next_count(fire_count, identical)
            text = alert_with_count(msg, count)
            action = "update-identical" if (identical and prev_ts) else ("update" if prev_ts else "post")
            print(f"Health alert: {key} (#{count}, {action}, cooldown={int(cooldown)}s)")
            new_ts = send(text, update_ts=prev_ts)
            _record_send(state)
            state[key] = {
                "active": True,
                "last_fired": now,
                "fire_count": count,
                "slack_ts": new_ts or prev_ts,
                "last_text": msg,
            }
        else:
            entry = dict(prev) if prev else {}
            entry["active"] = True
            if "fire_count" not in entry:
                entry["fire_count"] = fire_count
            if "last_text" not in entry:
                entry["last_text"] = msg
            state[key] = entry

    # Recovery: previously active, no longer tripped
    for key, prev in list(state.items()):
        if key == "_meta":
            continue
        if prev.get("active") and key not in alerts:
            last_rec = prev.get("last_recovery", 0)
            time_since_fire = now - prev.get("last_fired", 0)
            if (now - last_rec > RECOVERY_COOLDOWN_S and time_since_fire > 600
                    and _can_send(state)):
                print(f"Health recovered: {key}")
                # Prefer updating the original alert message to a recovery note
                recovery = _recovery_text(key)
                new_ts = send(recovery, update_ts=prev.get("slack_ts"))
                _record_send(state)
                state[key] = {
                    "active": False,
                    "last_fired": prev.get("last_fired", now),
                    "fire_count": 0,
                    "last_recovery": now,
                    "slack_ts": new_ts or prev.get("slack_ts"),
                    "last_text": "",          # clear so next alert starts count at 1
                }
            else:
                state[key] = {
                    "active": False,
                    "last_fired": prev.get("last_fired", now),
                    "fire_count": prev.get("fire_count", 0),
                    "last_recovery": last_rec,
                    "slack_ts": prev.get("slack_ts"),
                    "last_text": prev.get("last_text", ""),
                }

    try:
        _atomic_write_json(ALERT_STATE, state, indent=1)
    except OSError as e:
        logger.warning("could not write alert state %s: %s", ALERT_STATE, e)

def retention_pins(image_dir, legacy_pins=()):
    """Pins that gate retention for `image_dir`: legacy repo-wide pins plus this
    camera's own.

    `<image_dir>/pins.json` is the canonical file — it is what the API writes
    (api_server._pins_path) and what the web root serves, so retention reading
    it is the only way an API pin can survive. `<repo>/pins.json` predates
    per-camera scoping and is still honored, read-only, so an upgrade does not
    start deleting frames the user pinned before; nothing here writes it.
    """
    pins = set(legacy_pins)
    raw = load_json_file(os.path.join(image_dir, "pins.json"), [])
    if isinstance(raw, list):
        pins.update(raw)
    return pins


def apply_retention(image_dir, analysis_data, pins, protect_unanalyzed=None):
    """Delete images to honor the age, persist-cap, and disk budgets.

    Rules: pinned images are never deleted.
    Pass 1 (age): unpinned images whose camera clock (filename timestamp,
    falling back to mtime when unparseable) is older than max_age_days,
    except LLM-verified timeline visits (persistable).
    Pass 2 (persist cap): age-expired persistable frames may occupy at most
    persist_budget_pct of max_dir_gb (default 20%). Oldest extra go first.
    This is a ceiling, not a reservation — the rolling window always has
    the remaining 80% so new detections cannot be starved.
    Pass 3 (budget): if images+thumbs still exceed max_dir_gb, delete oldest
    negatives (including car-only), then YOLO-only detections, then
    persistable, then unanalyzed. Pins remain sacred. A non-positive
    max_dir_gb disables passes 2 and 3 rather than emptying the dir (#22), and
    while a catalog is only a partial recovery uncatalogued frames are exempt
    too — a lost row must never read as "delete this verified frame" (#29).
    Returns the set of deleted filenames."""
    now = time.time()
    deleted = set()
    thumbs = _thumb_sizes(image_dir)
    tz_name = configured_tz_name()
    if protect_unanalyzed is None:
        protect_unanalyzed = catalog_is_partial()

    entries = []
    for f in os.listdir(image_dir):
        if not f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
            continue
        try:
            st = os.stat(os.path.join(image_dir, f))
        except OSError:
            continue
        charged = st.st_size + thumbs.get(f, 0)
        # Age by the camera clock embedded in the filename (DST-aware,
        # configured tz); mtime is only a fallback when it cannot be parsed
        # (e.g. copies, re-encodes, names without a Hikvision timestamp).
        clock = filename_epoch(f, tz_name)
        entries.append((f, st.st_mtime if clock is None else clock, charged))

    def delete(f):
        for path in (os.path.join(image_dir, f), os.path.join(image_dir, "thumbs", f)):
            try:
                os.remove(path)
            except OSError as e:
                logger.debug("retention could not remove %s: %s", path, e)
        deleted.add(f)

    def is_persistable(f):
        return (f not in pins and f in analysis_data
                and retention_is_persistable(analysis_data[f]))

    def is_deletable_negative(f):
        return (f not in pins and f in analysis_data
                and not retention_is_keep_detection(analysis_data[f])
                and not retention_is_persistable(analysis_data[f]))

    def is_yolo_only(f):
        return (f not in pins and f in analysis_data
                and retention_is_keep_detection(analysis_data[f])
                and not retention_is_persistable(analysis_data[f]))

    # A frame with no row at all is "unanalysed backlog" — normally evictable.
    # But when analysis.json was only partially recovered from a truncated
    # file, the missing rows are lost data, not absent analysis: the frames
    # they described were LLM-verified and age/budget passes were deleting
    # real evidence (#29). Protect them until a full sweep rebuilds the rows.
    def is_uncatalogued(f):
        return protect_unanalyzed and f not in analysis_data

    if protect_unanalyzed:
        n_hidden = sum(1 for f in os.listdir(image_dir)
                       if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))
                       and f not in analysis_data)
        if n_hidden:
            print(f"Retention: {n_hidden} uncatalogued frame(s) in {image_dir} are "
                  f"protected — the catalog is a partial recovery")

    cutoff = now - MAX_AGE_DAYS * 86400
    # A budget of 0 (or, before the clamp in apply_settings, a negative one)
    # means "no disk budget configured" -- NOT "over budget". Every category
    # is evictable, so `total > budget` held for any non-empty dir and the pass
    # removed every frame it could reach, emptying the archive in one sweep
    # (#22). run_health_checks already guarded the same key with `if budget`;
    # both call sites now agree: a non-positive budget disables enforcement.
    try:
        budget = max(0.0, float(MAX_DIR_GB)) * 1024 ** 3
    except (TypeError, ValueError):
        budget = 0.0  # unusable value -> fail closed, keep the frames
    enforce_budget = budget > 0
    if not enforce_budget:
        logger.warning("max_dir_gb=%r leaves no disk budget; skipping the "
                       "disk-budget retention passes for %s", MAX_DIR_GB, image_dir)
    persist_budget = persist_budget_bytes() if enforce_budget else 0.0

    # Pass 1: max age — motion / YOLO-only / unanalyzed; keep LLM timeline
    for f, mtime, size in entries:
        if (mtime < cutoff and f not in pins and not is_persistable(f)
                and not is_uncatalogued(f)):
            delete(f)

    remaining = [e for e in entries if e[0] not in deleted]

    # Pass 2: ceiling on age-expired LLM timeline frames
    archived = sorted(
        (e for e in remaining if e[1] < cutoff and is_persistable(e[0])),
        key=lambda e: e[1])
    archive_bytes = sum(s for _, _, s in archived)
    if enforce_budget and archive_bytes > persist_budget:
        if persist_budget >= 1024 ** 2:
            print(f"Retention: persist archive {archive_bytes / 1024 ** 3:.2f}GiB "
                  f"> {persist_budget / 1024 ** 3:.2f}GiB cap in {image_dir}")
        for f, mtime, size in archived:
            if archive_bytes <= persist_budget:
                break
            delete(f)
            archive_bytes -= size

    remaining = [e for e in entries if e[0] not in deleted]
    total = sum(s for _, _, s in remaining)

    # Pass 3: total dir budget — rolling window wins over the persist archive
    if enforce_budget and total > budget:
        negatives = sorted((e for e in remaining if is_deletable_negative(e[0])),
                           key=lambda e: e[1])
        yolo_only = sorted((e for e in remaining if is_yolo_only(e[0])),
                           key=lambda e: e[1])
        persistable = sorted((e for e in remaining if is_persistable(e[0])),
                             key=lambda e: e[1])
        unanalyzed = [] if protect_unanalyzed else sorted(
            (e for e in remaining
             if e[0] not in pins and e[0] not in analysis_data),
            key=lambda e: e[1])
        for f, mtime, size in negatives + yolo_only + persistable:
            if total <= budget:
                break
            if f in deleted:
                continue
            delete(f)
            total -= size
        if total > budget and unanalyzed:
            print(f"Retention: still over budget after analyzed frames; "
                  f"evicting oldest unanalyzed from {image_dir}")
            for f, mtime, size in unanalyzed:
                if total <= budget:
                    break
                if f in deleted:
                    continue
                delete(f)
                total -= size

    # Orphan thumbs: parent image already gone (legacy deletes, API, etc.)
    orphan_n = 0
    orphan_bytes = 0
    parents = {e[0] for e in entries} - deleted
    for name, tsize in thumbs.items():
        if name in parents or name in deleted:
            continue
        try:
            os.remove(os.path.join(image_dir, "thumbs", name))
        except OSError:
            continue
        orphan_n += 1
        orphan_bytes += tsize

    if deleted or orphan_n:
        sizes = {f: s for f, _, s in entries}
        freed = sum(sizes.get(f, 0) for f in deleted) + orphan_bytes
        if deleted:
            print(f"Retention: removed {len(deleted)} images from {image_dir}")
        if orphan_n:
            print(f"Retention: removed {orphan_n} orphan thumbs from {image_dir}")
        _log_retention(image_dir, len(deleted) + orphan_n, freed)
    return deleted

def generate_thumbnails(image_dir, images):
    """Create missing thumbnails under <image_dir>/thumbs/ for grid view."""
    if cv2 is None:
        return
    thumb_dir = os.path.join(image_dir, "thumbs")
    os.makedirs(thumb_dir, exist_ok=True)
    made = 0
    for img in images:
        thumb_path = os.path.join(thumb_dir, img)
        if os.path.exists(thumb_path):
            continue
        try:
            im = cv2.imread(os.path.join(image_dir, img))
            if im is None:
                continue
            h, w = im.shape[:2]
            if w > THUMB_WIDTH:
                im = cv2.resize(im, (THUMB_WIDTH, max(1, round(h * THUMB_WIDTH / w))),
                                interpolation=cv2.INTER_AREA)
            cv2.imwrite(thumb_path, im, [cv2.IMWRITE_JPEG_QUALITY, 70])
            made += 1
        except Exception as e:
            print(f"Thumbnail failed for {img}: {e}")
    if made:
        print(f"Generated {made} thumbnails in {thumb_dir}")

def take_pipeline_lock(blocking=False):
    """Take the global pipeline lock. Returns the held file, or None if the
    lock is already held (or cannot be taken) — in which case the caller must
    not touch the watch dirs.

    Non-blocking by default on purpose: the cron --retention-only watchdog
    firing while a multi-hour sweep owns the lock must skip and retry on its
    next tick, not queue up behind the sweep and then run anyway. Returns None
    when the lock file cannot be opened, which fails closed: without the lock
    nothing can guarantee we are the only writer, so nothing gets deleted.
    """
    try:
        fh = open(PIPELINE_LOCK, "a")
    except OSError as e:
        logger.warning("could not open pipeline lock %s (%s); skipping this run",
                       PIPELINE_LOCK, e)
        return None
    flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(fh.fileno(), flags)
    except OSError:
        fh.close()
        return None
    return fh


# Exit codes. 0 = the sweep ran, or was a clean skip (busy lock, nothing
# configured yet); 2 = usage error, raised in __main__; 3 = settings.json could
# not be read or parsed, so the sweep was refused. 3 is deliberately distinct
# from 1/2: create-index.sh and tools/watchdog.sh stamp the lastrun marker
# only on 0, so a misconfigured box stops reporting a successful sweep and the
# watchdog's recent_sweep/ret_age heuristics go stale instead of reading the
# box as healthy (#27).
EXIT_OK = 0
EXIT_SETTINGS_ERROR = 3


def main(retention_only=False, rescan_days=None, settings_path=None):
    """Serialise every entry path on the global pipeline lock, then sweep.

    Returns a process exit code: 0 when the sweep ran or was a clean skip,
    ``EXIT_SETTINGS_ERROR`` when settings.json could not be read or parsed.

    The lock used to be taken only by the --rescan-days branch, so the cron
    --retention-only watchdog ran unlocked: it deleted frames while a live
    sweep was mid-cv2.imread on them, and the shared 1s-debounced catalog flush
    made the last writer win (#30). --rescan-days keeps its old blocking wait;
    every other path skips instead of deleting under a concurrent run.
    """
    # Re-read settings.json so /api/settings changes land this sweep. An explicit
    # settings_path aims the sweep at a specific file (tests, one-off tooling);
    # None keeps reading the deployed settings.json, so production is unchanged.
    #
    # Read it BEFORE the lock, and strictly: a half-written settings.json is a
    # fault, not a configuration, and must be reported as one even when another
    # run happens to hold the lock (the lock-busy path is a benign exit 0).
    # Bailing out here — before the lock, before apply_settings, before any
    # catalog is read or written — is what makes "deletes nothing, writes
    # nothing" structural rather than a promise (#27).
    try:
        cfg = load_settings(settings_path, strict=True)
    except SettingsError as e:
        logger.error("%s; refusing to sweep -- retention, health checks and "
                     "every disk budget would be configured from nothing", e)
        print(f"ERROR: {e}", file=sys.stderr)
        return EXIT_SETTINGS_ERROR
    lock = take_pipeline_lock(blocking=bool(rescan_days))
    if lock is None:
        print(f"Pipeline lock {PIPELINE_LOCK} is held by another run; skipping "
              f"this sweep (nothing analysed, nothing deleted).")
        return EXIT_OK
    try:
        return _run_sweep(cfg, retention_only=retention_only,
                          rescan_days=rescan_days)
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def _run_sweep(cfg, retention_only=False, rescan_days=None):
    global RATE_LIMITED
    apply_settings(cfg)
    RATE_LIMITED = False  # fresh budget each sweep; a throttle only pauses one sweep
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not WATCH_DIRS:
        print("No watch_dirs configured in settings.json - nothing to do.")
        return EXIT_OK
    watch_dirs = WATCH_DIRS
    base_dir = BASE_DIR
    analysis_file = os.path.join(base_dir, "analysis.json")
    burst_file = os.path.join(base_dir, "bursts.json")
    
    pins_file = os.path.join(base_dir, "pins.json")

    # Resilient load: a truncated analysis.json must not abort the sweep
    # before retention runs (that was the ENOSPC → corrupt → no cleanup loop).
    analysis_data = load_json_file(analysis_file, {}, catalog=True)
    if not isinstance(analysis_data, dict):
        analysis_data = {}
    # A partial recovery means rows are missing, not that frames are
    # unanalysed: exempt uncatalogued frames from retention for this sweep
    # instead of deleting evidence whose row was lost (#29).
    protect_unanalyzed = catalog_is_partial(analysis_file)
    if protect_unanalyzed:
        print(f"WARNING: {os.path.basename(analysis_file)} is a partial recovery "
              f"(recovered prefix only). Frames with no row are protected from "
              f"deletion until a full sweep re-analyses them.")
    burst_data = load_json_file(burst_file, {}, catalog=True)
    if not isinstance(burst_data, dict):
        burst_data = {}
    # Legacy repo-wide pins, read-only. The canonical per-camera pins.json is
    # loaded per watch dir by retention_pins() — reading only this file here is
    # what made retention delete frames the user had pinned through the API.
    pins_raw = load_json_file(pins_file, [])
    legacy_pins = set(pins_raw) if isinstance(pins_raw, list) else set()

    free_mem = get_free_mem_gb()
    ollama_up = ollama_available()
# The models THIS host can serve right now: cloud models need no RAM but
    # are only allowed when the allow_cloud kill switch is on; local models
    # need free_mem >= threshold. A cloud primary works on a low-RAM box.
    serve_chain = runnable_chain(MODEL_PRIMARY, MODEL_FALLBACK, free_mem, local_mem_threshold(), ALLOW_CLOUD)
    can_run_chain = ollama_up and bool(serve_chain)
    # Master gate for any LLM deep-pass/burst work this sweep
    llm_ready = DEEP_PASSES_ENABLED and (can_run_chain or (ALLOW_CLOUD and api_key))
    mode = "retention-only" if retention_only else (
        "ON" if DEEP_PASSES_ENABLED else "OFF (detector-only)")
    print(f"System Check: Free Memory = {free_mem:.1f}GB. Ollama up: {ollama_up}. "
          f"Runnable chain: {serve_chain or '[]'}. Cloud allowed: {ALLOW_CLOUD}. "
          f"Deep passes: {mode}. Burst summaries: "
          f"{'ON' if BURST_SUMMARIES_ENABLED else 'OFF'}")

    for image_dir in watch_dirs:
        if not os.path.exists(image_dir):
            continue
        print(f"Scanning {image_dir}...")
        
        if not rescan_days:
            apply_retention(image_dir, analysis_data,
                            retention_pins(image_dir, legacy_pins),
                            protect_unanalyzed=protect_unanalyzed)

        # Cron/watchdog path: honor age + disk budgets without re-entering
        # the multi-hour analysis queue (which can hold the global lock).
        if retention_only:
            print(f"Retention-only: skipped analysis for {image_dir}")
            continue

        images = [f for f in os.listdir(image_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))]
        # Chronological order is REQUIRED for burst detection below:
        # it groups by the gap between consecutive entries, and an
        # unsorted list yields negative gaps that chain unrelated
        # images (taken days apart) into one false "burst".
        images.sort(key=lambda x: os.path.getmtime(os.path.join(image_dir, x)))

        if not rescan_days:
            generate_thumbnails(image_dir, images)

        def _mtime(x):
            return os.path.getmtime(os.path.join(image_dir, x))

        rescan_mode = bool(rescan_days)
        if rescan_mode:
            # Detections only (person/dog/cat/bird). Empties and car-only stay put.
            queue = [
                i for i in images
                if filename_within_days(i, rescan_days, tz_name=configured_tz_name())
                and rec_is_urgent_detection(analysis_data.get(i) or {})
                # Resume: rows already written by this scan path have `_scans`.
                and not isinstance((analysis_data.get(i) or {}).get("_scans"), list)
            ]
            queue.sort(key=_mtime, reverse=True)
            print(f"Rescan last {rescan_days}d: {len(queue)} detections in {image_dir}")
        else:
            # Catch-up prioritization:
            # 1. Images not in analysis_data at all
            # 2. Images marked as "partial" (OpenCV hit, but no LLM yet)
            missing = [i for i in images if i not in analysis_data]
            partials = [i for i in images if i in analysis_data and analysis_data[i].get("fast_pass") == "partial"]
            queue = sorted(missing, key=_mtime, reverse=True) + sorted(partials, key=_mtime, reverse=True)

        if not queue:
            print(f"No pending analysis for {image_dir}")

        new_analysis = False
        deep_pass_count = 0
        max_deep_passes = MAX_DEEP_PASSES
        if rescan_mode:
            max_deep_passes = max(MAX_DEEP_PASSES, len(queue) * 4)

        for img in queue:
            image_path = os.path.join(image_dir, img)

            if rescan_mode:
                # Fresh YOLO (parked-car mask) then current e2b scans, even if
                # the row was already verified under an older schema.
                fp_results = fast_pass_dispatch(image_path)
                was_partial = False
            else:
                was_partial = img in analysis_data and analysis_data[img].get("fast_pass") == "partial"
                if was_partial:
                    fp_results = detector_labels(analysis_data[img])
                else:
                    fp_results = fast_pass_dispatch(image_path)

            if negative_fast_pass_row(fp_results) is None:
                continue
            if fp_results:
                if rescan_mode:
                    needs_deep = True
                else:
                    needs_deep = img not in analysis_data or analysis_data[img].get("fast_pass") == "partial"
                # YOLO person/dog/animal hits go to e2b. Car-only does not.
                urgent = llm_should_trigger(fp_results)

                if needs_deep and urgent:
                    shown = {k: v for k, v in (fp_results or {}).items()
                             if k != "_centres" and v is True}
                    print(f"Deep Pass Required for {img}: {shown}")
                    result = None
                    prev = analysis_data.get(img)
                    # Only attempt (and spend budget) when an engine is
                    # actually available; otherwise leave the partial in
                    # place for a later sweep instead of logging a
                    # zero-second failure.
                    if deep_pass_count < max_deep_passes and llm_ready:
                        # Stale stored labels (re-queued partials) force a
                        # fresh detector run so YOLO flags stay current
                        import scans
                        kind = camera_kind(image_path)
                        fresh_fp = None if was_partial else fp_results
                        # Multi-image timeline context: prepend prior frames
                        # from the same camera so e2b sees motion across time.
                        # collect_timeline_images already caps to 3 total
                        # (2 priors + current) when MULTI_IMAGE_3H is set.
                        timeline_images = None
                        if MULTI_IMAGE_ENABLED and not rescan_mode:
                            timeline_images = collect_timeline_images(
                                image_dir, img,
                                max_age_minutes=MULTI_IMAGE_3H,
                                max_images=3)
                        result, n_scans = run_deep_pass(
                            image_path, img, can_run_chain, api_key,
                            "priority", fp_labels=fresh_fp,
                            timeline_images=timeline_images)
                        deep_pass_count += n_scans
                        labels_for_scans, _centres = labels_and_centres(fp_results)
                        if result and result.get("_yolo"):
                            labels_for_scans = list(result["_yolo"])
                        asked = scans.scans_for(
                            kind, labels_for_scans, skip=scan_skip_ids(kind))
                        if result:
                            rec = merge_llm_into_fastpass(
                                fp_results, result, model=LAST_MODEL_USED,
                                duration_s=LAST_DURATION_S,
                                schema=scans.union_schema(asked, result))
                        elif n_scans == 0:
                            rec = detector_labels(fp_results)
                            rec["_llm_skip"] = "no_scan"
                        else:
                            rec = merge_llm_into_fastpass(
                                fp_results, None, skip_reason="llm_failed",
                                duration_s=LAST_DURATION_S)
                    else:
                        if not DEEP_PASSES_ENABLED:
                            skip = "llm_disabled"
                        elif not ollama_up:
                            skip = "ollama_down"
                        elif not serve_chain:
                            skip = "low_mem"
                        else:
                            skip = "budget"
                        rec = merge_llm_into_fastpass(
                            fp_results, None, skip_reason=skip)
                    persist_row(analysis_file, analysis_data, img, rec,
                                existed=img in analysis_data)
                    new_analysis = True
                    maybe_notify_urgent_frame(
                        img, analysis_data[img], image_path,
                        trigger="priority", prev=prev)
                    try:
                        import ha_mqtt
                        rec = analysis_data[img]
                        # Only retained-publish successful flag JSON. Skip-only
                        # (llm_failed / low_mem / ...) must not clobber HA.
                        if ha_mqtt.is_analysed(rec):
                            ha_mqtt.publish_record(img, rec, image_path)
                        else:
                            print(
                                "ha mqtt: not overwriting retained flags "
                                f"({rec.get('_llm_skip') or 'no analysis'})"
                            )
                    except Exception as e:
                        print(f"ha mqtt publish failed: {e}")
                elif needs_deep:
                    # Car-only / other non-trigger labels: persist detector
                    # and a skip reason. Do NOT mark partial (would re-queue).
                    rec = detector_labels(fp_results)
                    rec["_llm_skip"] = "no_trigger"
                    if analysis_data.get(img) != rec:
                        persist_row(analysis_file, analysis_data, img, rec,
                                    existed=img in analysis_data)
                        new_analysis = True
            else:
                rec = negative_fast_pass_row(fp_results)
                if rec is None:
                    continue
                persist_row(analysis_file, analysis_data, img, rec,
                            existed=img in analysis_data)
                new_analysis = True

            if deep_pass_count >= max_deep_passes:
                print(f"Batch limit ({max_deep_passes}) reached for {image_dir}")
                break

        if rescan_mode:
            if new_analysis:
                flush_analysis(analysis_file, analysis_data, force=True)
            print(f"Rescan done for {image_dir}: {deep_pass_count} e2b calls")
            continue

        # 1b. Idle backfill: spend any leftover deep-pass budget verifying
        # fast-pass negatives with the LLM, newest first, so the whole
        # archive eventually gets HA flags merged onto the detector record
        # (YOLO person/dog/car labels are never overwritten).
        if DEEP_BACKFILL and deep_pass_count < max_deep_passes and llm_ready:
            pool = [i for i in images if i in analysis_data and in_backfill_pool(analysis_data[i])]

            # Prioritize frames near existing detections: appear/disappear
            # boundaries live there, so verifying them first sharpens the
            # Timeline fast instead of grinding empty frames newest-first.
            def mtime(x):
                return os.path.getmtime(os.path.join(image_dir, x))

            detection_times = sorted(
                mtime(i) for i in images
                if i in analysis_data
                and is_llm_verified(analysis_data[i])
                and any(v is True for k, v in analysis_data[i].items() if k not in ("_yolo",))
            )

            def nearest_detection_gap(x):
                if not detection_times:
                    return 0
                t = mtime(x)
                pos = bisect.bisect_left(detection_times, t)
                best = float("inf")
                if pos < len(detection_times):
                    best = detection_times[pos] - t
                if pos > 0:
                    best = min(best, t - detection_times[pos - 1])
                return best

            # Closest-to-a-detection first; newest first as the tiebreak
            pool.sort(key=lambda x: (nearest_detection_gap(x), -mtime(x)))
            targets = pool[:max(0, max_deep_passes - deep_pass_count)]

            def _backfill_one(img):
                # A peer thread may trip the per-sweep rate-limit flag; honor it
                # so we stop spending passes against a throttled endpoint.
                if RATE_LIMITED:
                    return (img, None, 0)
                print(f"Backfill deep pass for {img}")
                res, n = run_deep_pass(os.path.join(image_dir, img), img,
                                       can_run_chain, api_key, "backfill")
                return (img, res, n)

            workers = concurrency_workers(DEEP_CONCURRENCY, len(targets))
            if workers <= 1:
                results = (_backfill_one(img) for img in targets)
            else:
                # Fan out cloud calls; results consumed here in the main thread,
                # so analysis_data / counters are never mutated concurrently.
                ex = ThreadPoolExecutor(max_workers=workers)
                results = ex.map(_backfill_one, targets)
            for img, result, n_scans in results:
                deep_pass_count += n_scans or 0
                if result:
                    import scans
                    prior = detector_labels(analysis_data.get(img) or {})
                    kind = camera_kind(os.path.join(image_dir, img))
                    labels_for_scans = list(result.get("_yolo") or detector_true_labels(prior))
                    asked = scans.scans_for(
                        kind, labels_for_scans, skip=scan_skip_ids(kind))
                    persist_row(
                        analysis_file, analysis_data, img,
                        merge_llm_into_fastpass(
                            prior, result, model=LAST_MODEL_USED,
                            duration_s=LAST_DURATION_S,
                            schema=scans.union_schema(asked, result)),
                        existed=True)
                    new_analysis = True
            if workers > 1:
                ex.shutdown(wait=True)

        # 2. Image Bursts Detection
        bursts = []
        if images:
            current_burst = [images[0]]
            for i in range(1, len(images)):
                t1 = os.path.getmtime(os.path.join(image_dir, images[i-1]))
                t2 = os.path.getmtime(os.path.join(image_dir, images[i]))
                if t2 - t1 < BURST_THRESHOLD_SECONDS:
                    current_burst.append(images[i])
                else:
                    if len(current_burst) >= 2:
                        bursts.append(current_burst)
                    current_burst = [images[i]]
            if len(current_burst) >= 2:
                bursts.append(current_burst)

        # 3. Burst Analysis (capped per sweep - each summary is minutes
        # of CPU inference; uncapped this can hold the lock for hours)
        burst_analyses = 0
        for burst in bursts:
            if burst_analyses >= 2:
                break
            burst_id = burst[-1] # Use last image as ID
            if burst_id in burst_data:
                continue
            
            # Only analyze burst if at least one image has a detection
            has_detection = any(img in analysis_data and any(v is True for k, v in analysis_data[img].items() if k != 'fast_pass') for img in burst)
            
            if has_detection and llm_ready and BURST_SUMMARIES_ENABLED:
                print(f"Analyzing burst ending at {burst_id}...")
                full_paths = [os.path.join(image_dir, f) for f in burst[-3:]] # Take last 3 max
                started = time.time()
                status_base = {"image": burst_id, "model": MODEL_PRIMARY,
                               "trigger": "burst", "started": started}
                set_inference_status(status_base)
                summary = None
                used = None
                if can_run_chain and not RATE_LIMITED:
                    # Stream the caption into inference_status.json so the UI sees
                    # it build live (surfaced via /api/status .inference.partial).
                    summary = analyze_burst_local(
                        full_paths,
                        on_progress=lambda acc: set_inference_status({**status_base, "partial": acc}))
                    if summary:
                        used = LAST_MODEL_USED   # the chain model that actually served
                if not summary and ALLOW_CLOUD and api_key:
                    used = MODEL_CLOUD
                    summary = analyze_burst_openrouter(full_paths, api_key)
                set_inference_status(None)
                log_inference(burst_id, used, started, time.time() - started,
                              ["burst summary"], summary is not None, "burst")
                burst_analyses += 1
                if summary:
                    burst_data[burst_id] = { "summary": summary, "images": burst }
                    new_analysis = True
                    try:
                        import pipeline_events
                        pipeline_events.emit("new-burst", id=burst_id, summary=summary)
                    except Exception as e:
                        logger.warning("could not emit new-burst event: %s", e)
                    # Fan the new contextual analysis out to integrations
                    # (Slack, ...). Prefer thumbnails for a lightweight clip;
                    # fully guarded so a notifier never breaks the sweep.
                    try:
                        from integrations import notify_burst
                        thumb_dir = os.path.join(image_dir, "thumbs")
                        frame_paths = [
                            os.path.join(thumb_dir, f) if os.path.exists(os.path.join(thumb_dir, f))
                            else os.path.join(image_dir, f)
                            for f in burst
                        ]
                        notify_burst(burst_id, summary, frame_paths)
                    except Exception as e:
                        print(f"Integration notify failed: {e}")

        if new_analysis:
            flush_analysis(analysis_file, analysis_data, force=True)
            try:
                _atomic_write_json(burst_file, burst_data, indent=2)
                print("Updated data files.")
            except OSError as e:
                print(f"Failed to write bursts (disk full?): {e}")

    # Prune analysis entries for images deleted by retention or the API.
    # A watch dir that is absent right now (unmounted volume, NFS blip, a
    # bind mount that is not up yet) makes every one of its frames look
    # deleted. The per-dir scan tolerates that (continue), but committing the
    # prune rewrote analysis.json to {} — destroying all HA flags, LLM
    # summaries and burst links for that camera, permanently (#23). Postpone
    # the prune until every configured dir is back.
    absent_dirs = [d for d in watch_dirs if not os.path.exists(d)]
    if absent_dirs:
        print(f"Watch dir(s) absent, catalog prune postponed (rows kept): "
              f"{', '.join(absent_dirs)}")
    existing = set()
    for d in watch_dirs:
        if os.path.exists(d):
            existing.update(os.listdir(d))
    catalog_partial = catalog_is_partial(analysis_file)
    stale = [] if absent_dirs else [k for k in analysis_data if k not in existing]
    if stale:
        for k in stale:
            del analysis_data[k]
        if catalog_partial:
            # Only ever commit a fully parsed catalog: rewriting a partially
            # recovered one in place discards the truncated tail for good
            # (#29). A full sweep re-analyses the affected frames and commits
            # the repaired catalog.
            print(f"Pruned {len(stale)} stale rows in memory only; the partially "
                  f"recovered {os.path.basename(analysis_file)} was not rewritten.")
        else:
            try:
                _atomic_write_json(analysis_file, analysis_data, indent=2)
                print(f"Pruned {len(stale)} stale analysis entries.")
            except OSError as e:
                print(f"Failed to write pruned analysis.json: {e}")

    # Prune burst entries that reference deleted images or that span
    # longer than the chain rule allows (false groups created before
    # the chronological-sort fix). They re-detect correctly next run.
    def burst_valid(b):
        imgs = b.get("images", [])
        if not imgs or any(i not in existing for i in imgs):
            return False
        times = []
        for i in imgs:
            for d in watch_dirs:
                p = os.path.join(d, i)
                if os.path.exists(p):
                    times.append(os.path.getmtime(p))
                    break
        max_span = BURST_THRESHOLD_SECONDS * max(1, len(imgs) - 1)
        return bool(times) and (max(times) - min(times)) <= max_span

    bad_bursts = ([] if absent_dirs
                  else [k for k, b in burst_data.items() if not burst_valid(b)])
    if bad_bursts:
        for k in bad_bursts:
            del burst_data[k]
        if catalog_is_partial(burst_file):
            print(f"Pruned {len(bad_bursts)} burst rows in memory only; the "
                  f"partially recovered {os.path.basename(burst_file)} was not "
                  f"rewritten.")
        else:
            try:
                _atomic_write_json(burst_file, burst_data, indent=2)
                print(f"Pruned {len(bad_bursts)} invalid burst entries.")
            except OSError as e:
                print(f"Failed to write pruned bursts.json: {e}")

    # Operational health alerts (debounced; pushed to Slack if configured)
    try:
        run_health_checks(watch_dirs, api_key)
    except Exception as e:
        print(f"Health check failed: {e}")
    return EXIT_OK

if __name__ == "__main__":
    # --publish-latest: one-shot retained MQTT of newest FRONT/BACK analyses.
    if "--publish-latest" in sys.argv:
        import ha_mqtt
        out = ha_mqtt.publish_latest()
        for kind, pair in out.items():
            ok, detail = pair
            status = "ok" if ok else "FAIL"
            print(f"{kind}: {status} {detail}")
        raise SystemExit(0 if out and any(ok for ok, _ in out.values()) else 2)
    rescan_days = None
    if "--rescan-days" in sys.argv:
        i = sys.argv.index("--rescan-days")
        try:
            rescan_days = int(sys.argv[i + 1])
        except (IndexError, ValueError):
            print("usage: analyze_images.py --rescan-days N", file=sys.stderr)
            raise SystemExit(2)
        if rescan_days < 1:
            print("--rescan-days must be >= 1", file=sys.stderr)
            raise SystemExit(2)
        print(f"Waiting for pipeline lock to rescan last {rescan_days}d of detections...")
        # main() takes the global pipeline lock (blocking for a rescan, which
        # is the one mode that legitimately waits for the current sweep).
        # Its exit code IS the process exit code: a refused sweep must not be
        # reported as a clean run (#27).
        raise SystemExit(main(rescan_days=rescan_days))
    # --retention-only: apply settings.json age/disk budgets + prune catalogs,
    # then exit. Used by the cron watchdog so cleanup never depends solely on
    # the long-lived create-index / analyze loop staying healthy. Same contract:
    # the watchdog stamps its lastrun marker only on exit 0.
    raise SystemExit(main(retention_only=("--retention-only" in sys.argv)))
