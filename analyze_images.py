import os
import requests
import json
import base64
import sys
import cv2
import numpy as np
import time
import random
import bisect
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# CONFIGURATION (defaults; override in settings.json next to this script)
MODEL_CLOUD = "google/gemma-4-31b-it"
MODEL_LOCAL = "gemma4:12b"  # Ollama tag (verified; "gemma-4:12b" does not exist)
# Primary/fallback inference, both via Ollama (:11434). The primary may be a
# fast cloud model (e.g. minimax-m3:cloud); the fallback a local/private model
# (e.g. gemma4:e4b). On failure or rate-limit the chain falls through.
MODEL_PRIMARY = MODEL_LOCAL  # default; overridden by settings `model_primary`
MODEL_FALLBACK = ""          # optional; settings `model_fallback`
BURST_THRESHOLD_SECONDS = 300  # Group images within 5 mins
MIN_MEM_FOR_LOCAL_GB = 16.0
MAX_AGE_DAYS = 30   # retention: no-detection images older than this are removed
MAX_DIR_GB = 4.0    # retention: per-camera disk budget
ALLOW_CLOUD = True  # permit OpenRouter calls when local inference is unavailable
OLLAMA_URL = "http://localhost:11434"
MAX_DEEP_PASSES = 15  # LLM calls (local or cloud) per camera per sweep
DEEP_CONCURRENCY = 1   # parallel backfill deep passes; >1 only sane for a cloud
                       # model (no local RAM contention). Rate-limit backoff guards it.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FAST_PASS_ENGINE = "yolo"  # "yolo" (recommended) or "haar" (legacy cascades)
YOLO_DIR = os.path.join(BASE_DIR, "models", "yolo")
YOLO_CONF = 0.45
DEEP_BACKFILL = True  # idle sweeps spend leftover LLM budget verifying negatives, newest first
DEEP_PASSES_ENABLED = True  # master switch for ALL Gemma/LLM work (priority + backfill + bursts);
                            # set false to run detector-only and free CPU/RAM
WATCH_DIRS = []  # REQUIRED via settings.json watch_dirs - deployment specific
# Labels that alone do NOT trigger an urgent deep pass (e.g. a car parked
# in frame 24/7); they're recorded and verified later by the backfill.
GATE_IGNORE_LABELS = ["car"]

_settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
if os.path.exists(_settings_path):
    try:
        _s = json.load(open(_settings_path))
        BURST_THRESHOLD_SECONDS = _s.get("burst_threshold_seconds", BURST_THRESHOLD_SECONDS)
        MAX_AGE_DAYS = _s.get("max_age_days", MAX_AGE_DAYS)
        MAX_DIR_GB = _s.get("max_dir_gb", MAX_DIR_GB)
        MIN_MEM_FOR_LOCAL_GB = _s.get("min_mem_for_local_gb", MIN_MEM_FOR_LOCAL_GB)
        ALLOW_CLOUD = _s.get("allow_cloud", ALLOW_CLOUD)
        OLLAMA_URL = _s.get("ollama_url", OLLAMA_URL)
        MAX_DEEP_PASSES = _s.get("max_deep_passes", MAX_DEEP_PASSES)
        DEEP_CONCURRENCY = _s.get("deep_concurrency", DEEP_CONCURRENCY)
        MODEL_LOCAL = _s.get("model_local", MODEL_LOCAL)
        MODEL_PRIMARY = _s.get("model_primary", MODEL_LOCAL)  # default to model_local
        MODEL_FALLBACK = _s.get("model_fallback", "")
        FAST_PASS_ENGINE = _s.get("fast_pass_engine", FAST_PASS_ENGINE)
        DEEP_BACKFILL = _s.get("deep_backfill", DEEP_BACKFILL)
        DEEP_PASSES_ENABLED = _s.get("deep_passes_enabled", DEEP_PASSES_ENABLED)
        GATE_IGNORE_LABELS = _s.get("gate_ignore_labels", GATE_IGNORE_LABELS)
        WATCH_DIRS = _s.get("watch_dirs", WATCH_DIRS)
        YOLO_DIR = _s.get("yolo_dir", YOLO_DIR)
    except (ValueError, OSError) as e:
        print(f"Warning: could not read settings.json ({e}); using defaults")

# Initialize Haar Cascades
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
body_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_fullbody.xml')
cat_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalcatface.xml')

def fast_pass(image_path):
    try:
        img = cv2.imread(image_path)
        if img is None: return {}
        h, w = img.shape[:2]
        scale = 400.0 / w
        small = cv2.resize(img, (400, int(h * scale)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        
        results = {}
        # Increased sensitivity: lower minNeighbors, smaller minSize
        faces = face_cascade.detectMultiScale(gray, 1.1, 3, minSize=(20, 20))
        if len(faces) > 0: results["face"] = True
            
        bodies = body_cascade.detectMultiScale(gray, 1.1, 2, minSize=(40, 80))
        if len(bodies) > 0: results["body"] = True
            
        cats = cat_cascade.detectMultiScale(gray, 1.1, 2, minSize=(20, 20))
        if len(cats) > 0: results["cat"] = True
            
        return results
    except Exception as e:
        print(f"Fast pass error: {e}")
        return {}

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
    try:
        img = cv2.imread(image_path)
        if img is None:
            return {}
        ids, confs, _ = _load_yolo().detect(img, confThreshold=YOLO_CONF, nmsThreshold=0.4)
        results = {}
        for cid in np.array(ids).flatten():
            label = YOLO_CLASSES.get(int(cid))
            if label:
                results[label] = True
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

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

DETECT_PROMPT = "Analyze this webcam image. Specifically detect if any PERSON, FACE, BODY, DOG, CAT, or BIRD is visible. If you see a human (even partial), use keys 'person', 'face', or 'body'. For an animal use 'dog', 'cat', or 'bird'. If you see something unusual (e.g. alien_ufo), add a descriptive key for it. Return ONLY a valid JSON object with boolean keys for detected items, PLUS - only if a person, animal, bird, or vehicle is present - a 'description' key with a brief (max 12 words) caption of what is happening. Omit 'description' for empty scenes. Example: {\"person\": true, \"face\": true, \"dog\": false, \"description\": \"person in dark jacket walking toward the gate\"}"

def analyze_image_openrouter(image_path, api_key):
    base64_image = encode_image(image_path)
    headers = { "Authorization": f"Bearer {api_key}", "Content-Type": "application/json" }

    payload = {
        "model": MODEL_CLOUD,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": DETECT_PROMPT
                    },
                    {
                        "type": "image_url",
                        "image_url": { "url": f"data:image/jpeg;base64,{base64_image}" }
                    }
                ]
            }
        ],
        "response_format": { "type": "json_object" }
    }
    
    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, data=json.dumps(payload), timeout=60)
        if response.status_code != 200: return None
        content = response.json()['choices'][0]['message']['content'].strip()
        return json.loads(content)
    except:
        return None

# --- Inference audit trail (read by api_server for the UI) ---
INFERENCE_LOG = os.path.join(BASE_DIR, "inference_log.json")
INFERENCE_STATUS = os.path.join(BASE_DIR, "inference_status.json")

# Serializes the small audit-trail files so concurrent deep passes (and the
# live API reader) never see a half-written or interleaved JSON file.
_IO_LOCK = threading.Lock()


def _atomic_write_json(path, data, indent=None):
    """Write JSON to `path` via temp-file + os.replace so a reader never
    observes a partially written file, and a crash mid-write can't truncate
    the real one. Caller holds _IO_LOCK when ordering vs other writers matters."""
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


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


def load_json_file(path, default=None):
    """Load JSON from disk. On corruption (e.g. truncated mid-write), attempt
    recovery and rewrite a clean file so the next sweep does not re-fail.
    Returns a fresh empty dict/list when the file is missing or unrecoverable
    — never raises for parse errors."""
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
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as e:
        print(f"Warning: {path} is corrupt ({e}); attempting recovery")
    recovered = _recover_truncated_json(raw, empty)
    # Sentinel: recovery failed when we got back the empty default object we
    # passed in *and* the file clearly had substantial content we couldn't parse.
    n = len(recovered) if hasattr(recovered, "__len__") else 0
    if n == 0 and len(raw) > 8:
        # One more check: did recovery genuinely yield an empty container, or
        # did _recover_truncated_json give up and return `empty`?
        if recovered is empty:
            print(f"Could not recover {path}; starting from empty")
            return empty
    print(f"Recovered {path}: retained {n} entries; rewriting clean copy")
    try:
        _atomic_write_json(path, recovered, indent=2)
    except OSError as we:
        print(f"Warning: could not rewrite recovered {path}: {we}")
    return recovered


def set_inference_status(payload):
    """Live marker of the in-flight LLM call ({} when idle)."""
    try:
        with _IO_LOCK:
            _atomic_write_json(INFERENCE_STATUS, payload or {})
    except OSError:
        pass

def log_inference(image, model, started, duration, labels, ok, trigger):
    with _IO_LOCK:
        try:
            log = json.load(open(INFERENCE_LOG)) if os.path.exists(INFERENCE_LOG) else []
        except (OSError, ValueError):
            log = []
        log.append({"image": image, "model": model, "trigger": trigger,
                    "started": started, "duration_s": round(duration, 1),
                    "labels": labels, "ok": ok})
        try:
            _atomic_write_json(INFERENCE_LOG, log[-200:], indent=1)
        except OSError:
            pass

def run_deep_pass(image_path, img_name, can_run_chain, api_key, trigger, fp_labels=None):
    """One audited LLM deep pass: local first, cloud fallback.

    The successful verdict carries a "_yolo" field with the fast-pass
    detector's labels so the UI can require detector+LLM consensus.
    fp_labels=None means "run the detector fresh" (used for re-queued
    partials whose stored labels may be stale, and for backfill)."""
    # Once the endpoint has throttled us this sweep, stop spending deep passes
    # against it — resume on the next sweep (~60s later). No hammering.
    if RATE_LIMITED:
        return None
    started = time.time()
    set_inference_status({"image": img_name, "model": MODEL_PRIMARY,
                          "trigger": trigger, "started": started})
    result = None
    used = None
    if can_run_chain:
        result = analyze_image_local(image_path)
        if result is not None:
            used = LAST_MODEL_USED   # the chain model that actually served
    if not result and ALLOW_CLOUD and api_key:
        used = MODEL_CLOUD
        result = analyze_image_openrouter(image_path, api_key)
    set_inference_status(None)
    if isinstance(result, dict):
        if fp_labels is None:
            fp = fast_pass_dispatch(image_path) or {}
            fp_labels = sorted(k for k, v in fp.items() if v is True)
        result["_yolo"] = fp_labels
    labels = sorted(k for k, v in (result or {}).items() if v is True)
    log_inference(img_name, used, started, time.time() - started,
                  labels, result is not None, trigger)
    # Per-image notify (objects/all modes) — ONLY for freshly-queued frames,
    # never the idle backfill of the historical archive (would be a flood).
    # Guarded: a notifier failure must never break the sweep.
    if isinstance(result, dict) and trigger == "priority":
        try:
            from integrations import notify_image
            notify_image(img_name, labels, result.get("description", ""), image_path)
        except Exception as e:
            print(f"notify_image failed: {e}")
    return result

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
                              # (so logs/status show real cloud-vs-local, not a guess)
LLM_MAX_RETRIES = 4           # attempts before giving up a single call
LLM_BACKOFF_BASE = 2.0        # seconds; doubles each retry
LLM_BACKOFF_CAP = 30.0        # cap a single wait (sweep lock has a 1h timeout)


class RateLimited(Exception):
    """The LLM endpoint throttled us after exhausting backoff retries."""


def backoff_delay(attempt, base=LLM_BACKOFF_BASE, cap=LLM_BACKOFF_CAP):
    """Exponential backoff for retry `attempt` (0-based), capped. Pure."""
    return min(cap, base * (2 ** attempt))


def _ollama_chat(payload, timeout):
    """POST /api/chat with exponential-backoff retry on 429/503. Returns the
    response on success; raises RateLimited if still throttled after
    LLM_MAX_RETRIES. Other request errors propagate to the caller."""
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


def runnable_chain(primary, fallback, free_gb, min_local_gb):
    """model_chain() filtered to the models THIS host can actually serve:
    cloud models always; a local model only when free_gb >= min_local_gb.
    Pure. Without this, a low-RAM box silently does zero deep passes even
    though its configured cloud primary needs no local memory."""
    return [m for m in model_chain(primary, fallback)
            if model_is_cloud(m) or free_gb >= min_local_gb]


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
    payload = {**payload, "stream": True}
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


def analyze_image_local(image_path):
    """Vision inference via Ollama, walking the primary->fallback model chain.
    Falls through on rate-limit/error; only bails the sweep (RATE_LIMITED) if the
    WHOLE chain was throttled."""
    global RATE_LIMITED, LAST_MODEL_USED
    base = {
        "messages": [{
            "role": "user",
            "content": DETECT_PROMPT,
            "images": [encode_image(image_path)],
        }],
        "stream": False,
        "format": "json",
    }
    rate_limited_all, tried = True, 0
    for model in runnable_chain(MODEL_PRIMARY, MODEL_FALLBACK, get_free_mem_gb(), MIN_MEM_FOR_LOCAL_GB):
        tried += 1
        try:
            response = _ollama_chat({**base, "model": model}, timeout=600)
            result = json.loads(response.json()["message"]["content"])
            if isinstance(result, dict):
                LAST_MODEL_USED = model   # record what actually served
                return result
            return None
        except RateLimited:
            continue  # try the next model in the chain
        except requests.exceptions.ConnectionError:
            rate_limited_all = False
            return None  # Ollama itself is down; nothing in the chain will work
        except Exception as e:
            rate_limited_all = False
            print(f"Inference failed on {model}: {e}")
            continue
    if tried and rate_limited_all:
        RATE_LIMITED = True
        print("LLM rate-limited across the model chain; backing off for this sweep")
    return None

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
    for model in runnable_chain(MODEL_PRIMARY, MODEL_FALLBACK, get_free_mem_gb(), MIN_MEM_FOR_LOCAL_GB):
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
    except:
        return "Failed to analyze sequence."

def get_free_mem_gb():
    try:
        with open('/proc/meminfo', 'r') as f:
            lines = f.readlines()
            free = [l for l in lines if l.startswith('MemAvailable')][0]
            return int(free.split()[1]) / (1024 * 1024)
    except:
        return 0

THUMB_WIDTH = 320

def has_detection(entry):
    return any(v is True for k, v in entry.items() if k != 'fast_pass')

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
    except OSError:
        pass

ALERT_STATE = os.path.join(BASE_DIR, "alert_state.json")
# Belt-and-braces Slack rate limiting for health alerts (esp. disk/space spam)
ALERT_BASE_COOLDOWN_S = 1800          # 30 min base; doubles with each re-fire of same key
ALERT_MAX_COOLDOWN_S = 12 * 3600      # 12 h ceiling
RECOVERY_COOLDOWN_S = 3600            # 1 h between recovery notices for the same key
MAX_ALERTS_PER_WINDOW = 4             # hard global cap on alert/recovery sends per window
ALERT_WINDOW_S = 3600                 # the window for the global max

def _scan_dir(image_dir):
    """Newest image mtime and total bytes for a camera dir (one scandir)."""
    newest, total = 0.0, 0
    try:
        with os.scandir(image_dir) as it:
            for e in it:
                if not (e.is_file() and e.name.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))):
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                total += st.st_size
                newest = max(newest, st.st_mtime)
    except OSError:
        pass
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

def run_health_checks(watch_dirs, api_key):
    """Evaluate pipeline / camera / storage health and push DEBOUNCED Slack
    alerts (plus recovery notices) through the integrations layer. Runs once
    per sweep; fully guarded so it can never break the pipeline."""
    now = time.time()
    try:
        offline_hours = json.load(open(_settings_path)).get("camera_offline_hours", 12)
    except Exception:
        offline_hours = 12
    offline_s = offline_hours * 3600

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
        log = json.load(open(INFERENCE_LOG))
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
    except Exception:
        notify_alert = None

    def send(msg):
        if notify_alert:
            try:
                notify_alert(msg)
            except Exception as e:
                print(f"Alert send failed: {e}")

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

    # Fire new alerts with exponential backoff + global max-queue
    for key, msg in alerts.items():
        prev = state.get(key, {})
        fire_count = int(prev.get("fire_count", 0))
        cooldown = min(ALERT_BASE_COOLDOWN_S * (2 ** min(fire_count, 5)), ALERT_MAX_COOLDOWN_S)
        due = (not prev.get("active")) or (now - prev.get("last_fired", 0) > cooldown)
        if due and _can_send(state):
            print(f"Health alert: {key} (fire#{fire_count + 1}, cooldown={int(cooldown)}s)")
            send(msg)
            _record_send(state)
            state[key] = {"active": True, "last_fired": now, "fire_count": fire_count + 1}
        else:
            # Keep active so recovery logic stays correct; do not re-fire
            entry = dict(prev) if prev else {}
            entry["active"] = True
            if "fire_count" not in entry:
                entry["fire_count"] = fire_count
            state[key] = entry

    # Recovery: previously active, no longer tripped (with its own cooldown)
    for key, prev in list(state.items()):
        if key == "_meta":
            continue
        if prev.get("active") and key not in alerts:
            last_rec = prev.get("last_recovery", 0)
            time_since_fire = now - prev.get("last_fired", 0)
            if (now - last_rec > RECOVERY_COOLDOWN_S and time_since_fire > 600
                    and _can_send(state)):
                print(f"Health recovered: {key}")
                send(_recovery_text(key))
                _record_send(state)
                state[key] = {"active": False, "last_fired": prev.get("last_fired", now),
                              "fire_count": 0, "last_recovery": now}
            else:
                # Silence recovery spam on rapid flaps; clear active for next cycle
                state[key] = {"active": False, "last_fired": prev.get("last_fired", now),
                              "fire_count": prev.get("fire_count", 0),
                              "last_recovery": last_rec}

    try:
        _atomic_write_json(ALERT_STATE, state, indent=1)
    except OSError:
        pass

def apply_retention(image_dir, analysis_data, pins):
    """Delete images to honor the age and disk budgets.

    Rules: pinned images are never deleted.
    Pass 1 (age): analyzed no-detection images older than max_age_days.
    Pass 2 (budget): if over max_dir_gb, oldest negatives first, then oldest
    detections.
    Pass 3 (budget escape): if still over budget (e.g. large unanalyzed
    backlog while the catalog was down), oldest unanalyzed frames go next.
    Unanalyzed frames are otherwise kept so they can be reviewed first.
    Returns the set of deleted filenames."""
    now = time.time()
    deleted = set()

    entries = []
    for f in os.listdir(image_dir):
        if not f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
            continue
        try:
            st = os.stat(os.path.join(image_dir, f))
        except OSError:
            continue
        entries.append((f, st.st_mtime, st.st_size))

    def delete(f):
        for path in (os.path.join(image_dir, f), os.path.join(image_dir, "thumbs", f)):
            try:
                os.remove(path)
            except OSError:
                pass
        deleted.add(f)

    def is_deletable_negative(f):
        return (f not in pins and f in analysis_data
                and not has_detection(analysis_data[f]))

    # Pass 1: max age - only analyzed images with no identified objects
    cutoff = now - MAX_AGE_DAYS * 86400
    for f, mtime, size in entries:
        if mtime < cutoff and is_deletable_negative(f):
            delete(f)

    # Pass 2: disk budget - oldest negatives first, then oldest detected
    remaining = [e for e in entries if e[0] not in deleted]
    total = sum(s for _, _, s in remaining)
    budget = MAX_DIR_GB * 1024 ** 3
    if total > budget:
        negatives = sorted((e for e in remaining if is_deletable_negative(e[0])),
                           key=lambda e: e[1])
        detected = sorted((e for e in remaining
                           if e[0] not in pins and e[0] in analysis_data
                           and has_detection(analysis_data[e[0]])),
                          key=lambda e: e[1])
        for f, mtime, size in negatives + detected:
            if total <= budget:
                break
            delete(f)
            total -= size

    # Pass 3: still over budget — unanalyzed frames can pin the dir forever
    # if the catalog/pipeline was down. Drop oldest unanalyzed (never pins).
    if total > budget:
        unanalyzed = sorted(
            (e for e in remaining
             if e[0] not in deleted and e[0] not in pins
             and e[0] not in analysis_data),
            key=lambda e: e[1])
        if unanalyzed:
            print(f"Retention: still over budget after analyzed frames; "
                  f"evicting oldest unanalyzed from {image_dir}")
        for f, mtime, size in unanalyzed:
            if total <= budget:
                break
            delete(f)
            total -= size

    if deleted:
        sizes = {f: s for f, _, s in entries}
        freed = sum(sizes.get(f, 0) for f in deleted)
        print(f"Retention: removed {len(deleted)} images from {image_dir}")
        _log_retention(image_dir, len(deleted), freed)
    return deleted

def generate_thumbnails(image_dir, images):
    """Create missing thumbnails under <image_dir>/thumbs/ for grid view."""
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

def main(retention_only=False):
    global RATE_LIMITED
    RATE_LIMITED = False  # fresh budget each sweep; a throttle only pauses one sweep
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not WATCH_DIRS:
        print("No watch_dirs configured in settings.json - nothing to do.")
        return
    watch_dirs = WATCH_DIRS
    base_dir = BASE_DIR
    analysis_file = os.path.join(base_dir, "analysis.json")
    burst_file = os.path.join(base_dir, "bursts.json")
    
    pins_file = os.path.join(base_dir, "pins.json")

    # Resilient load: a truncated analysis.json must not abort the sweep
    # before retention runs (that was the ENOSPC → corrupt → no cleanup loop).
    analysis_data = load_json_file(analysis_file, {})
    if not isinstance(analysis_data, dict):
        analysis_data = {}
    burst_data = load_json_file(burst_file, {})
    if not isinstance(burst_data, dict):
        burst_data = {}
    pins_raw = load_json_file(pins_file, [])
    pins = set(pins_raw) if isinstance(pins_raw, list) else set()

    free_mem = get_free_mem_gb()
    ollama_up = ollama_available()
    # The models THIS host can serve right now: cloud models need no RAM, local
    # models need free_mem >= threshold. A cloud primary works on a low-RAM box.
    serve_chain = runnable_chain(MODEL_PRIMARY, MODEL_FALLBACK, free_mem, MIN_MEM_FOR_LOCAL_GB)
    can_run_chain = ollama_up and bool(serve_chain)
    # Master gate for any LLM deep-pass/burst work this sweep
    llm_ready = DEEP_PASSES_ENABLED and (can_run_chain or (ALLOW_CLOUD and api_key))
    mode = "retention-only" if retention_only else (
        "ON" if DEEP_PASSES_ENABLED else "OFF (detector-only)")
    print(f"System Check: Free Memory = {free_mem:.1f}GB. Ollama up: {ollama_up}. "
          f"Runnable chain: {serve_chain or '[]'}. OpenRouter cloud: {ALLOW_CLOUD}. "
          f"Deep passes: {mode}")

    for image_dir in watch_dirs:
        if not os.path.exists(image_dir): continue
        print(f"Scanning {image_dir}...")
        
        apply_retention(image_dir, analysis_data, pins)

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

        generate_thumbnails(image_dir, images)

        # Catch-up prioritization:
        # 1. Images not in analysis_data at all
        # 2. Images marked as "partial" (OpenCV hit, but no LLM yet)
        missing = [i for i in images if i not in analysis_data]
        partials = [i for i in images if i in analysis_data and analysis_data[i].get("fast_pass") == "partial"]
        
        # Combine: newest missing first, then partials
        queue = sorted(missing, key=lambda x: os.path.getmtime(os.path.join(image_dir, x)), reverse=True) + partials
        
        if not queue:
            print(f"No pending analysis for {image_dir}")

        new_analysis = False
        deep_pass_count = 0
        max_deep_passes = MAX_DEEP_PASSES # Batch size (local AND cloud count)

        for img in queue:
            image_path = os.path.join(image_dir, img)

            # If it's a partial, we already have fp_results
            was_partial = img in analysis_data and analysis_data[img].get("fast_pass") == "partial"
            if was_partial:
                fp_results = {k:v for k,v in analysis_data[img].items()
                              if k not in ("fast_pass", "_yolo", "description")}
            else:
                fp_results = fast_pass_dispatch(image_path)

            if fp_results:
                needs_deep = img not in analysis_data or analysis_data[img].get("fast_pass") == "partial"
                # A hit consisting only of ignored labels (e.g. the
                # permanently parked car) is recorded but not urgent -
                # the idle backfill verifies it later.
                urgent = any(k not in GATE_IGNORE_LABELS for k, v in fp_results.items() if v is True)

                if needs_deep and urgent:
                    print(f"Deep Pass Required for {img}: {fp_results}")
                    result = None
                    # Only attempt (and spend budget) when an engine is
                    # actually available; otherwise leave the partial in
                    # place for a later sweep instead of logging a
                    # zero-second failure.
                    if deep_pass_count < max_deep_passes and llm_ready:
                        # Stale stored labels (re-queued partials) force a
                        # fresh detector run for the consensus record
                        fresh_fp = None if was_partial else \
                            sorted(k for k, v in fp_results.items() if v is True)
                        result = run_deep_pass(image_path, img, can_run_chain, api_key,
                                               "priority", fp_labels=fresh_fp)
                        deep_pass_count += 1

                    if result:
                        analysis_data[img] = result
                        new_analysis = True
                    else:
                        # Still partial (limit reached or failed)
                        analysis_data[img] = {**fp_results, "fast_pass": "partial"}
                        new_analysis = True
                elif needs_deep:
                    partial = {**fp_results, "fast_pass": "partial"}
                    if analysis_data.get(img) != partial:
                        analysis_data[img] = partial
                        new_analysis = True
            else:
                # Negative fast pass
                analysis_data[img] = { "fast_pass": "negative" }
                new_analysis = True

            if deep_pass_count >= max_deep_passes:
                print(f"Batch limit ({max_deep_passes}) reached for {image_dir}")
                break

        # 1b. Idle backfill: spend any leftover deep-pass budget verifying
        # fast-pass negatives with the LLM, newest first, so the whole
        # archive eventually gets a Gemma verdict (which replaces the
        # fast-pass marker - the LLM result always trumps the detector).
        if DEEP_BACKFILL and deep_pass_count < max_deep_passes and llm_ready:
            def awaiting_backfill(entry):
                if entry.get("fast_pass") == "negative":
                    return True
                # Partials whose only hits are ignored labels (parked car)
                return entry.get("fast_pass") == "partial" and \
                    all(k in GATE_IGNORE_LABELS for k, v in entry.items() if v is True)

            pool = [i for i in images if i in analysis_data and awaiting_backfill(analysis_data[i])]

            # Prioritize frames near existing detections: appear/disappear
            # boundaries live there, so verifying them first sharpens the
            # Timeline fast instead of grinding empty frames newest-first.
            mtime = lambda x: os.path.getmtime(os.path.join(image_dir, x))
            detection_times = sorted(
                mtime(i) for i in images
                if i in analysis_data
                and "fast_pass" not in analysis_data[i]  # an LLM verdict
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
                    return (img, None)
                print(f"Backfill deep pass for {img}")
                return (img, run_deep_pass(os.path.join(image_dir, img), img,
                                           can_run_chain, api_key, "backfill"))

            workers = concurrency_workers(DEEP_CONCURRENCY, len(targets))
            if workers <= 1:
                results = (_backfill_one(img) for img in targets)
            else:
                # Fan out cloud calls; results consumed here in the main thread,
                # so analysis_data / counters are never mutated concurrently.
                ex = ThreadPoolExecutor(max_workers=workers)
                results = ex.map(_backfill_one, targets)
            for img, result in results:
                deep_pass_count += 1
                if result:
                    analysis_data[img] = result
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
                    if len(current_burst) >= 2: bursts.append(current_burst)
                    current_burst = [images[i]]
            if len(current_burst) >= 2: bursts.append(current_burst)

        # 3. Burst Analysis (capped per sweep - each summary is minutes
        # of CPU inference; uncapped this can hold the lock for hours)
        burst_analyses = 0
        for burst in bursts:
            if burst_analyses >= 2:
                break
            burst_id = burst[-1] # Use last image as ID
            if burst_id in burst_data: continue
            
            # Only analyze burst if at least one image has a detection
            has_detection = any(img in analysis_data and any(v is True for k, v in analysis_data[img].items() if k != 'fast_pass') for img in burst)
            
            if has_detection and llm_ready:
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
            try:
                _atomic_write_json(analysis_file, analysis_data, indent=2)
                _atomic_write_json(burst_file, burst_data, indent=2)
                print(f"Updated data files.")
            except OSError as e:
                print(f"Failed to write analysis/bursts (disk full?): {e}")

    # Prune analysis entries for images deleted by retention or the API
    existing = set()
    for d in watch_dirs:
        if os.path.exists(d):
            existing.update(os.listdir(d))
    stale = [k for k in analysis_data if k not in existing]
    if stale:
        for k in stale:
            del analysis_data[k]
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

    bad_bursts = [k for k, b in burst_data.items() if not burst_valid(b)]
    if bad_bursts:
        for k in bad_bursts:
            del burst_data[k]
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

if __name__ == "__main__":
    # --retention-only: apply settings.json age/disk budgets + prune catalogs,
    # then exit. Used by the cron watchdog so cleanup never depends solely on
    # the long-lived create-index / analyze loop staying healthy.
    main(retention_only=("--retention-only" in sys.argv))
