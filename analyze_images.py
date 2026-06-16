import os
import requests
import json
import base64
import sys
import cv2
import numpy as np
import time
import bisect
from datetime import datetime

# CONFIGURATION (defaults; override in settings.json next to this script)
MODEL_CLOUD = "google/gemma-4-31b-it"
MODEL_LOCAL = "gemma4:12b"  # Ollama tag (verified; "gemma-4:12b" does not exist)
BURST_THRESHOLD_SECONDS = 300  # Group images within 5 mins
MIN_MEM_FOR_LOCAL_GB = 16.0
MAX_AGE_DAYS = 30   # retention: no-detection images older than this are removed
MAX_DIR_GB = 4.0    # retention: per-camera disk budget
ALLOW_CLOUD = True  # permit OpenRouter calls when local inference is unavailable
OLLAMA_URL = "http://localhost:11434"
MAX_DEEP_PASSES = 15  # LLM calls (local or cloud) per camera per sweep
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FAST_PASS_ENGINE = "yolo"  # "yolo" (recommended) or "haar" (legacy cascades)
YOLO_DIR = os.path.join(BASE_DIR, "models", "yolo")
YOLO_CONF = 0.45
DEEP_BACKFILL = True  # idle sweeps spend leftover LLM budget verifying negatives, newest first
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
        MODEL_LOCAL = _s.get("model_local", MODEL_LOCAL)
        FAST_PASS_ENGINE = _s.get("fast_pass_engine", FAST_PASS_ENGINE)
        DEEP_BACKFILL = _s.get("deep_backfill", DEEP_BACKFILL)
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

def set_inference_status(payload):
    """Live marker of the in-flight LLM call ({} when idle)."""
    try:
        with open(INFERENCE_STATUS, "w") as f:
            json.dump(payload or {}, f)
    except OSError:
        pass

def log_inference(image, model, started, duration, labels, ok, trigger):
    try:
        log = json.load(open(INFERENCE_LOG)) if os.path.exists(INFERENCE_LOG) else []
    except (OSError, ValueError):
        log = []
    log.append({"image": image, "model": model, "trigger": trigger,
                "started": started, "duration_s": round(duration, 1),
                "labels": labels, "ok": ok})
    try:
        with open(INFERENCE_LOG, "w") as f:
            json.dump(log[-200:], f, indent=1)
    except OSError:
        pass

def run_deep_pass(image_path, img_name, can_run_local, api_key, trigger, fp_labels=None):
    """One audited LLM deep pass: local first, cloud fallback.

    The successful verdict carries a "_yolo" field with the fast-pass
    detector's labels so the UI can require detector+LLM consensus.
    fp_labels=None means "run the detector fresh" (used for re-queued
    partials whose stored labels may be stale, and for backfill)."""
    started = time.time()
    model = MODEL_LOCAL if can_run_local else MODEL_CLOUD
    set_inference_status({"image": img_name, "model": model,
                          "trigger": trigger, "started": started})
    result = None
    used = None
    if can_run_local:
        used = MODEL_LOCAL
        result = analyze_image_local(image_path)
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

def analyze_image_local(image_path):
    """Vision inference via a local Ollama server (if one is running)."""
    try:
        payload = {
            "model": MODEL_LOCAL,
            "messages": [{
                "role": "user",
                "content": DETECT_PROMPT,
                "images": [encode_image(image_path)],
            }],
            "stream": False,
            "format": "json",
        }
        response = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=600)
        response.raise_for_status()
        result = json.loads(response.json()["message"]["content"])
        return result if isinstance(result, dict) else None
    except requests.exceptions.ConnectionError:
        return None  # No Ollama server; fall through to cloud
    except Exception as e:
        print(f"Local inference failed: {e}")
        return None

BURST_PROMPT = "These webcam frames were taken in sequence. Describe what happens across them - any people, animals, birds, vehicles, or notable changes in the scene (lighting, objects moving). Don't assume a person is the subject. If nothing meaningfully changes, say so in one sentence."

def analyze_burst_local(image_paths):
    """Burst summary via local Ollama (multi-image message)."""
    try:
        payload = {
            "model": MODEL_LOCAL,
            "messages": [{
                "role": "user",
                "content": BURST_PROMPT,
                "images": [encode_image(p) for p in image_paths],
            }],
            "stream": False,
        }
        response = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=900)
        response.raise_for_status()
        return response.json()["message"]["content"].strip()
    except Exception as e:
        print(f"Local burst analysis failed: {e}")
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
    try:
        log = json.load(open(RETENTION_LOG)) if os.path.exists(RETENTION_LOG) else []
    except (ValueError, OSError):
        log = []
    log.append({"ts": time.time(), "dir": os.path.basename(image_dir.rstrip("/")),
                "count": count, "bytes_freed": bytes_freed})
    try:
        with open(RETENTION_LOG, "w") as f:
            json.dump(log[-100:], f, indent=1)
    except OSError:
        pass

ALERT_STATE = os.path.join(BASE_DIR, "alert_state.json")
ALERT_COOLDOWN_S = 6 * 3600  # don't re-alert a persistent condition more often

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

def run_health_checks(watch_dirs, can_run_local, api_key):
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
    # Inference capability: dead if neither local nor cloud can run
    if not can_run_local and not (ALLOW_CLOUD and api_key):
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

    try:
        state = json.load(open(ALERT_STATE)) if os.path.exists(ALERT_STATE) else {}
    except (ValueError, OSError):
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

    # Fire new alerts (or re-fire after the cooldown)
    for key, msg in alerts.items():
        prev = state.get(key, {})
        if (not prev.get("active")) or (now - prev.get("last_fired", 0) > ALERT_COOLDOWN_S):
            print(f"Health alert: {key}")
            send(msg)
            state[key] = {"active": True, "last_fired": now}
        else:
            state[key]["active"] = True

    # Recovery: previously active, no longer tripped
    for key, prev in list(state.items()):
        if prev.get("active") and key not in alerts:
            print(f"Health recovered: {key}")
            send(_recovery_text(key))
            state[key]["active"] = False

    try:
        with open(ALERT_STATE, "w") as f:
            json.dump(state, f, indent=1)
    except OSError:
        pass

def apply_retention(image_dir, analysis_data, pins):
    """Delete images to honor the age and disk budgets.

    Rules: pinned images are never deleted; unanalyzed images are never
    deleted (they haven't been looked at yet); no-detection images go
    first, oldest first; images WITH detections are only deleted if the
    disk budget is still exceeded after that.
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

def main():
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not WATCH_DIRS:
        print("No watch_dirs configured in settings.json - nothing to do.")
        return
    watch_dirs = WATCH_DIRS
    base_dir = BASE_DIR
    analysis_file = os.path.join(base_dir, "analysis.json")
    burst_file = os.path.join(base_dir, "bursts.json")
    
    pins_file = os.path.join(base_dir, "pins.json")

    analysis_data = json.load(open(analysis_file)) if os.path.exists(analysis_file) else {}
    burst_data = json.load(open(burst_file)) if os.path.exists(burst_file) else {}
    pins = set(json.load(open(pins_file))) if os.path.exists(pins_file) else set()

    free_mem = get_free_mem_gb()
    can_run_local = free_mem >= MIN_MEM_FOR_LOCAL_GB and ollama_available()
    print(f"System Check: Free Memory = {free_mem:.1f}GB. Local LLM Enabled: {can_run_local}. Cloud Enabled: {ALLOW_CLOUD}")

    for image_dir in watch_dirs:
        if not os.path.exists(image_dir): continue
        print(f"Scanning {image_dir}...")
        
        apply_retention(image_dir, analysis_data, pins)

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
                    if deep_pass_count < max_deep_passes and \
                            (can_run_local or (ALLOW_CLOUD and api_key)):
                        # Stale stored labels (re-queued partials) force a
                        # fresh detector run for the consensus record
                        fresh_fp = None if was_partial else \
                            sorted(k for k, v in fp_results.items() if v is True)
                        result = run_deep_pass(image_path, img, can_run_local, api_key,
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
        if DEEP_BACKFILL and deep_pass_count < max_deep_passes and \
                (can_run_local or (ALLOW_CLOUD and api_key)):
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
            for img in pool:
                if deep_pass_count >= max_deep_passes:
                    break
                image_path = os.path.join(image_dir, img)
                print(f"Backfill deep pass for {img}")
                result = run_deep_pass(image_path, img, can_run_local, api_key, "backfill")
                deep_pass_count += 1
                if result:
                    analysis_data[img] = result
                    new_analysis = True

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
            
            if has_detection and (can_run_local or (ALLOW_CLOUD and api_key)):
                print(f"Analyzing burst ending at {burst_id}...")
                full_paths = [os.path.join(image_dir, f) for f in burst[-3:]] # Take last 3 max
                started = time.time()
                set_inference_status({"image": burst_id, "model": MODEL_LOCAL if can_run_local else MODEL_CLOUD,
                                      "trigger": "burst", "started": started})
                summary = None
                used = None
                if can_run_local:
                    used = MODEL_LOCAL
                    summary = analyze_burst_local(full_paths)
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
                        notify_burst(burst_id, summary, frame_paths, image_dir=image_dir)
                    except Exception as e:
                        print(f"Integration notify failed: {e}")

        if new_analysis:
            with open(analysis_file, 'w') as f: json.dump(analysis_data, f, indent=2)
            with open(burst_file, 'w') as f: json.dump(burst_data, f, indent=2)
            print(f"Updated data files.")

    # Prune analysis entries for images deleted by retention or the API
    existing = set()
    for d in watch_dirs:
        if os.path.exists(d):
            existing.update(os.listdir(d))
    stale = [k for k in analysis_data if k not in existing]
    if stale:
        for k in stale:
            del analysis_data[k]
        with open(analysis_file, 'w') as f: json.dump(analysis_data, f, indent=2)
        print(f"Pruned {len(stale)} stale analysis entries.")

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
        with open(burst_file, 'w') as f: json.dump(burst_data, f, indent=2)
        print(f"Pruned {len(bad_bursts)} invalid burst entries.")

    # Operational health alerts (debounced; pushed to Slack if configured)
    try:
        run_health_checks(watch_dirs, can_run_local, api_key)
    except Exception as e:
        print(f"Health check failed: {e}")

if __name__ == "__main__":
    main()
