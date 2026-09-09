# Deployment (this box)

Deployment-specific material: how *this* server runs the app. The app
itself (see [DEVELOP.md](DEVELOP.md)) only needs: a directory of images
per camera, a web server pointing at it, something invoking
`create-index.sh` per camera, and an Ollama endpoint. This file is the
candidate for extraction into a private infra repo, together with
`systemd/`, `docker-compose.yml`, and `settings.json`.

## Image ingestion & triggering

Hikvision cameras upload motion snapshots via FTP (as user `hikvision`)
into `/mnt/models/Webcam21` (webcam) and `/mnt/models/Webcam22` (dogcam).

**Triggering: inotify is recommended over polling.** `create-index.sh`
uses `inotifywait -m -e create` for instant reaction to new uploads,
plus a 60s idle loop that drains the backfill queue and acts as a
fallback if inotify misses events. On platforms without inotify
(network mounts, macOS), the idle loop alone degrades gracefully to
60s polling. The UI's ℹ status panel shows whether inotify is live.

## Hosting

Stock-nginx container (`docker-compose.yml`): camera dirs bind-mounted
**read-only** at port 8180, with Webcam21 files at `/` and Webcam22
files at `/Webcam22/`. All writes (pins, deletes, settings) go through
`api_server.py` on :8190, which runs on the host.

## Services

Installed by `sudo bash systemd/install.sh`:

| Unit | What |
|------|------|
| `webcam-pipeline@Webcam21/22` | create-index.sh watcher per camera |
| `webcam-api` | write API on :8190 |
| `ollama` | local LLM server |

All run as user **user** (cv2 is installed only for that user — as
root the pipeline dies with `ModuleNotFoundError: cv2`).

### Cron watchdog (safety net)

Systemd alone can report a unit **active** while a sweep is stuck for
hours (global flock held by a long analyze, corrupt catalog, etc.). An
**independent cron** watches health and forces app-configured cleanup so
we are not solely dependent on the long-lived processes.

Full behaviour, env vars, and decision rules:
**[DEVELOP.md — Cron watchdog](DEVELOP.md#cron-watchdog-toolswatchdogsh)** and
**[DEVELOP.md — Retention](DEVELOP.md#retention-file-rotation)**.

#### Install

```bash
sudo bash /home/user/webcam/systemd/install.sh
# or only the cron file:
sudo install -m 644 /home/user/webcam/systemd/webcam-watchdog.cron \
  /etc/cron.d/webcam-watchdog
chmod +x /home/user/webcam/tools/watchdog.sh
```

`install.sh` also strips legacy `@reboot create-index.sh` lines from the
`user` user crontab (systemd owns those) and any old
`webcam/tools/watchdog` user-crontab entries (the system cron.d file is
canonical).

#### Schedule (`/etc/cron.d/webcam-watchdog`)

| When | Command | Purpose |
|------|---------|---------|
| Every 15 minutes | `tools/watchdog.sh check` | Restart dead units; if `/tmp/webcam_analysis.lastrun` (or API `last_sweep_age_s`) is older than **2 hours**, restart both pipeline units (at most once per 2h — anti-thrash file `/tmp/webcam_watchdog_last_restart`) |
| Minute 5 of every hour | `tools/watchdog.sh retention` | Run `analyze_images.py --retention-only` under `/tmp/webcam_analysis.lock`, using **settings.json** (`max_age_days`, `max_dir_gb`) and **pins.json**. Not a blind `find -mtime` wipe |

Both run as user **user**, append to
`/home/user/webcam/watchdog.log`, and also log with syslog tag
`webcam-watchdog`.

#### Manual

```bash
/home/user/webcam/tools/watchdog.sh check
/home/user/webcam/tools/watchdog.sh retention
/home/user/webcam/tools/watchdog.sh auto   # check + retention if thresholds trip
```

#### Verify

```bash
# Cron installed?
cat /etc/cron.d/webcam-watchdog

# Health (503 = degraded but body still valid — do not use curl -f alone)
curl -sS http://127.0.0.1:8190/api/health | jq .
curl -sS http://127.0.0.1:8190/api/status | jq '{trigger, cameras, retention, filesystem}'

# Watchdog log
tail -50 /home/user/webcam/watchdog.log
journalctl -t webcam-watchdog -n 50

# Force cleanup now
/home/user/webcam/tools/watchdog.sh retention
```

#### Prerequisites on this box

- Passwordless `sudo systemctl restart …` for user `user` (unit restarts).
  Without it, check still runs retention logic but logs restart failures.
- `curl`, `python3`, `flock`, `fuser` (psmisc) on `PATH`.
- API listening on `127.0.0.1:8190` (`webcam-api.service`).

## Ollama (local LLM)

Standalone binary — NOT the official installer, NOT docker:
- binary: `/mnt/models/ollama-bin/bin/ollama` (v0.30.7 arm64 tarball)
- blobs: `/mnt/models/ollama/models` via `OLLAMA_MODELS` (pinned in the
  unit; the 45G root disk cannot hold models)
- model on this box: **`gemma4:e2b`**, about **~40 s/image** on this 4-core
  ARM CPU (Nice=19 so it never starves the box). `gemma4:12b` at
  ~5.5 min/image is **not** the live tag here.

The official install script was once run by accident: it puts ~2GB in
`/usr/local/lib/ollama` and installs a broken unit (nonexistent
`ollama` user). `systemd/install.sh` removes those leftovers.

## YOLO model files

`/mnt/models/yolo/yolov4-tiny.{cfg,weights}` (24MB) from the
AlexeyAB/darknet GitHub releases; path configured as `yolo_dir`.

## Disk layout

- `/` (45G) — system + docker; chronically tight. 12GB of dead k8s
  images live in containerd's k8s.io namespace (kubelet inactive,
  microk8s uninstalled); free with:
  `sudo bash -c 'ctr -n k8s.io images ls -q | xargs -r ctr -n k8s.io images rm'`
- `/mnt/models` (45G) — camera images, thumbnails, Ollama blobs, YOLO,
  plus other projects/cache. Typical large consumers (orders vary):
  `ollama/`, `projects/`, `Webcam21/`, `Webcam22/`, `ollama-bin/`,
  `cache/`, swap files.

### How retention maps to this disk

- **`max_dir_gb` (live 5.0)** applies **per camera directory** (sum of
  image files **plus matching thumbnails**), not to the whole volume
  and not to `/`. JSON/HTML/guide copies in the dir are not counted.
- **`max_age_days` (live 30)** deletes unpinned **non-timeline** images
  older than that. LLM-verified timeline visits may outlive this, but
  only up to **`persist_budget_pct` (live 20)** of `max_dir_gb` so the
  archive cannot fill the camera dir. Pins in `pins.json` are kept.
- Two cameras at budget ≈ 10 GB of JPEGs; the rest of `/mnt/models` is
  models and other data. Host ENOSPC can still happen while each camera
  looks “under budget”.
- Control-plane JSON (`analysis.json` etc.) lives under
  `/home/user/webcam` on **`/`**. Atomic rewrites need free space on
  root; a full root disk is how catalogs used to truncate mid-write.
- Hourly `tools/watchdog.sh retention` is the rock-solid cleanup path
  when the long analyze holds the flock for hours.

## History / scar tissue

- Legacy cleanup jobs in **root's crontab** + `/etc/cron.d/server-maintenance`
  (`find ... -mtime +10 -exec rm`) silently deleted images — including
  verified person shots — behind the retention system's back. Removed
  2026-06-12. If images vanish without `Retention: removed` log lines,
  check other users' crontabs first. **Do not reintroduce blind find/rm**
  for camera dirs; use `analyze_images.py --retention-only` /
  `tools/watchdog.sh retention`.
- **Corrupt `analysis.json` (ENOSPC mid-write)** used to crash every sweep
  before retention. Fixed with atomic writes + load-time recovery; the
  watchdog hourly path is the operational backstop. See DEVELOP retention
  section.
- `/etc/cron.d/server-maintenance` still runs a monthly
  `docker system prune -af` (1st, 3:30am) and a weekly disk `df` report —
  neither should touch camera JPEGs.
