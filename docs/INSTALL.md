# Install

How to stand up the funes-vision pipeline on a host. The reference
deployment is **Linux + systemd** (Debian/Ubuntu family; the units and
`systemd/install.sh` use a dedicated service user configured via
`/etc/webcam/webcam.env` — see `systemd/webcam.env.example`).
macOS/Windows are not supported for the watcher/retention services.

The pipeline itself is Python 3 (stdlib + a few third-party packages);
detection and HA flag inference use OpenCV and a local Ollama server.

## 1. System packages

```bash
sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv python3-pip \
  inotify-tools \
  jq \
  ffmpeg \
  util-linux \
  nodejs npm
```

| Package | Why |
|---------|-----|
| `inotify-tools` | `create-index.sh` watches each camera dir with `inotifywait`. |
| `jq` | `create-index.sh` builds `images.json` from the file list. |
| `ffmpeg` | `integrations/media.py` MP4 fallback for burst clips (`PIL` writes the GIF). |
| `util-linux` | `flock(1)` — the single-execution gate around every analysis sweep. |
| `python3-venv` | Isolated install of `requirements.txt`. |
| `nodejs npm` | Optional JS tooling / generated demo bundle; the Python test suite does not need it (see §6). |

## 2. Python environment

```bash
cd /path/to/webcam
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

`requirements.txt` pins the four real third-party runtime imports:
`requests`, `opencv-python` (`cv2`), `numpy`, and `Pillow` (`PIL`). The HA
MQTT publisher (`ha_mqtt.py`) speaks raw MQTT 3.1.1 over `socket`, so it
needs **no** `paho-mqtt` dependency.

Point the systemd units at this interpreter, e.g.
`ExecStart=/path/to/webcam/.venv/bin/python ...` if you adopt a venv — the
shipped units call the system `python3` by default.

## 3. YOLOv4-tiny weights

The fast pass loads two files from the directory in the **`yolo_dir`**
setting (`analyze_images.py` default: `<repo>/models/yolo`, overridable in
`settings.json`). COCO class ids are hard-coded, so no `coco.names` is
required — only:

```
yolov4-tiny.cfg
yolov4-tiny.weights
```

Download both into `yolo_dir`:

```bash
YOLO_DIR="$(python3 -c 'import json;print(json.load(open("settings.json")).get("yolo_dir","models/yolo"))')"
mkdir -p "$YOLO_DIR"
curl -L -o "$YOLO_DIR/yolov4-tiny.cfg" \
  https://raw.githubusercontent.com/AlexeyAB/darknet/master/cfg/yolov4-tiny.cfg
curl -L -o "$YOLO_DIR/yolov4-tiny.weights" \
  https://github.com/AlexeyAB/darknet/releases/download/yolov4/yolov4-tiny.weights
```

If the weights are missing, `fast_pass_engine: "yolo"` logs
`YOLO unavailable; falling back to Haar cascades` and quality drops. Keep
`fast_pass_engine` set to `"yolo"` (the default) for the intended detector.

## 4. Ollama and the vision model

The deep pass's HA flag inference goes through Ollama at the **`ollama_url`**
setting
(default `http://localhost:11434`). Install it, then pull the model named by
**`model_local`** / **`model_primary`** (default `gemma4:e2b`; set
`model_fallback` for an optional secondary tag and `allow_cloud` stays
`false` unless you deliberately want cloud inference).

```bash
curl -fsSL https://ollama.com/install.sh | sh

# Match settings.json: model_primary (falling back to model_local).
MODEL="$(python3 -c 'import json;s=json.load(open("settings.json"));print(s.get("model_primary",s.get("model_local","gemma4:e2b")))')"
ollama pull "$MODEL"

# Keep the model resident between sweeps (matches ollama_keep_alive).
sudo mkdir -p /etc/systemd/system/ollama.service.d
printf '[Service]\nEnvironment=OLLAMA_KEEP_ALIVE=24h\n' \
  | sudo tee /etc/systemd/system/ollama.service.d/keep-alive.conf
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

Set `deep_passes_enabled: false` in `settings.json` to run detector-only
(no LLM, no Ollama) if the box lacks RAM; `min_mem_for_local_gb` gates local
models automatically.

## 5. Configure and start

1. Create `settings.json` at the repo root (gitignored) with at minimum
   `watch_dirs` (the camera directories the watcher and sweeps read). See
   the settings reference in `DEVELOP.md` for every key
   (`watch_dirs`, `yolo_dir`, `ollama_url`, `model_primary`,
   `fast_pass_engine`, `max_age_days`, `max_dir_gb`, …).
2. Install the systemd units:
   ```bash
   sudo bash systemd/install.sh
   ```
   This enables `webcam-pipeline@<dir>` per camera, `webcam-api`, `ollama`,
   `webcam-compose` (the gallery container) and the
   `webcam-healthcheck.{service,timer}` pair, and installs the watchdog cron.
3. Serve the gallery via `docker-compose.yml` (nginx, read-only mounts) or
   your own web root.

## 6. Tests

Two suites, both runnable from a clean checkout after
`pip install -r requirements.txt` (the Python tests import `cv2`):

```bash
python3 -m unittest discover -s tests   # backend helpers
bash tests/run.sh                       # SPA helper suites + inline-JS syntax gate
```

`tests/run.sh` additionally needs `nodejs`; install `nodejs`/`npm` only if you
run the JS tooling, the screenshot harness, or rebuild the static demo bundle
(`tools/demo/`).

A clean checkout reports `Ran 405 tests … OK (skipped=2)`. The two skips are
`test_zones` cases that assert this box's actual zone triangles and are skipped
with `live settings.json not present` — the deployment-specific `settings.json`
is gitignored, so that is the expected result off-host, not a failure. Copy your
own `settings.json` in and they run for real.

## 7. Ingest contract (any tool that writes JPEGs)

Any external capture tool can feed the pipeline by writing stills into a
watched directory. Contract:

- **Location** — any directory listed in `watch_dirs` (the reference host
  uses `/mnt/models/Webcam21` and `/mnt/models/Webcam22`).
- **Formats** — `.jpg`, `.jpeg`, `.png`, `.gif`.
- **Filename** — include a `_YYYYMMDDHHMMSSmmm_` segment, e.g.
  `front_20240103153000123_00.jpg`. The timestamp is parsed DST-aware in
  the `timezone` setting (default `Australia/Sydney`) and drives age/retention
  and visit timeline logic. Files without a matching stamp still ingest, but
  fall back to filesystem `mtime` for ordering.
- **Write atomically** — write to a temp name and `rename(2)` into place, or
  write outside the watched tree and `mv` in, so `inotifywait` and the sweep
  never read a half-written JPEG.
- **Idempotence** — the pipeline owns `images.json`, `analysis.json`, pins,
  and ret/thumbnail sidecars in that directory; ingest tools must only create
  image files and never touch those catalogs.
