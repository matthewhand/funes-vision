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

Two stock-nginx containers (`docker-compose.yml`): camera dirs bind-
mounted **read-only** at ports 8180/8280. All writes (pins, deletes,
settings) go through `api_server.py` on :8190, which runs on the host.

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
hours (global flock held by a long analyze, corrupt catalog, etc.). A
cron job — independent of the service processes — watches and repairs:

| Schedule | Action |
|----------|--------|
| `*/15` | `tools/watchdog.sh check` — restart dead units; if last successful sweep is older than 2h, restart both pipeline units |
| `:05` hourly | `tools/watchdog.sh retention` — run `analyze_images.py --retention-only` under the same flock, using **settings.json** (`max_age_days`, `max_dir_gb`, pins). Not a blind `find -mtime` wipe |

Installed as `/etc/cron.d/webcam-watchdog` by `install.sh`. Log:
`/home/user/webcam/watchdog.log` (also syslog tag `webcam-watchdog`).

Manual:
```bash
/home/user/webcam/tools/watchdog.sh check
/home/user/webcam/tools/watchdog.sh retention
/home/user/webcam/tools/watchdog.sh auto   # check + retention if needed
```

## Ollama (local LLM)

Standalone binary — NOT the official installer, NOT docker:
- binary: `/mnt/models/ollama-bin/bin/ollama` (v0.30.7 arm64 tarball)
- blobs: `/mnt/models/ollama/models` via `OLLAMA_MODELS` (pinned in the
  unit; the 45G root disk cannot hold models)
- model: `gemma4:12b` (7.6GB). ~5.5 min/image on this 4-core ARM CPU,
  Nice=19 so it never starves the box.

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
- `/mnt/models` (45G) — camera images, thumbnails, Ollama blobs, YOLO.

## History / scar tissue

- Legacy cleanup jobs in **root's crontab** + `/etc/cron.d/server-maintenance`
  (`find ... -mtime +10 -exec rm`) silently deleted images — including
  verified person shots — behind the retention system's back. Removed
  2026-06-12. If images vanish without `Retention: removed` log lines,
  check other users' crontabs first.
- `/etc/cron.d/server-maintenance` still runs a monthly
  `docker system prune -af` (1st, 3:30am).
