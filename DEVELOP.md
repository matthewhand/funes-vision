# Developer & Operations Guide

Architecture, data formats, configuration, and operational detail for the
webcam AI gallery. For end-user usage see [README.md](README.md).

## System overview

```
Hikvision cameras ──FTP──> /mnt/models/Webcam21 (webcam)   nginx :8180 (ro)
                           /mnt/models/Webcam22 (dogcam)   nginx :8280 (ro)
                                │
                  inotifywait (create-index.sh, one per camera, systemd)
                                │  + 60s idle loop
                                ▼
                       analyze_images.py  (global flock, one at a time)
                        │ 1. retention (age + disk budget, pin-aware)
                        │ 2. thumbnails (320px JPEG -> thumbs/)
                        │ 3. fast pass: YOLO (yolov4-tiny) or Haar
                        │ 4. deep pass: gemma4:12b via local Ollama
                        │    (OpenRouter fallback if allow_cloud)
                        │ 5. burst detection + LLM burst summaries
                        │ 6. stale-entry pruning
                        ▼
            analysis.json / bursts.json / pins.json / images.json
                     (synced into each camera web root)
                                ▲
       api_server.py :8190  (pin / delete / settings - the ONLY
        write channel; web roots are mounted read-only in nginx)
                                ▲
                       index.html (single-file SPA)
```

Hosting: two stock-nginx docker containers (`docker-compose.yml`) with
read-only bind mounts. Deploying UI changes = copy `index.html` into both
web roots (see Deployment). The host paths are owned by the `hikvision`
FTP user but world-writable, so the pipeline (user `user`) can write.

## Components

### create-index.sh `<IMAGE_DIR>`
Watcher + orchestrator, one instance per camera (`webcam-pipeline@.service`).
- Regenerates `images.json` (newest-first file listing).
- Sources `~/.litellm/.env` for `OPENROUTER_API_KEY` (cloud fallback only).
- Runs `analyze_images.py`, then syncs `index.html` + JSON artifacts into
  the camera web root.
- Triggers: startup, every new image (inotify, 5s debounce), and a 60s
  idle loop ("catch-up sweep").
- **Global lock** `/tmp/webcam_analysis.lock` (flock, 1h timeout): the
  Python script scans BOTH camera dirs and writes shared JSON, so two
  instances must never run it concurrently. Do not make this per-camera.

### analyze_images.py
Single sweep, run fresh each time (config/code changes apply next sweep,
no restarts needed). Per camera dir, in order:

1. **Retention** (`apply_retention`) — see Retention below.
2. **Thumbnails** — missing `thumbs/<file>` generated at 320px q70.
   GIFs are skipped (cv2 can't read them); the UI falls back to full res.
3. **Queue build** — chronological listing (REQUIRED for burst grouping:
   the detector compares consecutive mtimes; unsorted listings produce
   negative gaps that chain images days apart into fake bursts).
   Priority: missing images newest-first, then partials.
4. **Fast pass** (`fast_pass_dispatch`) — YOLO by default, Haar fallback
   (auto, or via `fast_pass_engine`). YOLO = yolov4-tiny via OpenCV DNN,
   COCO classes mapped to person/car/cat/dog, conf 0.45, ~0.2s/image.
   Haar = legacy frontal-face/fullbody/frontalcatface cascades.
5. **Deep pass** — gemma4:12b through local Ollama (`analyze_image_local`,
   /api/chat with `format: json`); falls back to OpenRouter only when
   `allow_cloud` is true. Budgeted: `max_deep_passes` per camera per sweep
   counts local AND cloud calls. **An LLM verdict replaces the entry
   wholesale — the LLM always trumps the fast-pass detector.**
   - *Urgency gate*: a fast-pass hit consisting only of
     `gate_ignore_labels` (default `["car"]` — a car parked in frame 24/7)
     is recorded as a partial but does NOT consume urgent budget.
6. **Idle backfill** (`deep_backfill`) — leftover budget verifies
   fast-pass negatives and ignored-label partials, newest first, so the
   entire archive converges on LLM verdicts ("work backwards").
7. **Bursts** — consecutive images < `burst_threshold_seconds` apart form
   a burst; bursts containing a detection get an LLM sequence summary
   (last 3 frames). Invalid cached bursts (missing files, or spans
   violating the chain rule) are pruned and re-detected. A new summary is
   fanned out to enabled notifiers — see [Integrations](#integrations).
8. **Pruning** — analysis/burst entries whose files no longer exist
   (retention, API delete, external cleanup) are removed.

Inference timing on this box (4-core ARM, no GPU): **~5.5 min/image**.
`max_deep_passes` = 4 keeps a both-camera sweep (~45 min) under the 1h
lock timeout.

### Retention (replaces the old cron cleanups)
Runs once per sweep inside the lock. Two passes:
1. **Age**: analyzed images with no detections older than `max_age_days`.
2. **Disk budget**: if the camera dir exceeds `max_dir_gb`, delete oldest
   no-detection images first, then oldest detected images only if still
   over budget.
Never deleted: **pinned** images (pins.json) and **unanalyzed** images.
Thumbnails are deleted alongside.

> History: legacy `find ... -mtime +10 -exec rm` jobs in *root's* crontab
> (and `/etc/cron.d/server-maintenance`) used to delete images behind this
> system's back — including verified person images. Removed 2026-06-12.
> If images vanish without `Retention: removed`/`Pruned` log lines, check
> other users' crontabs first.

### api_server.py (port 8190)
Stdlib-only HTTP server, the single write channel (nginx mounts are ro).
- `GET  /api/pins` → pinned filenames
- `POST /api/pin` `{filename, pinned}` → updates pins.json (synced to
  both web roots immediately)
- `POST /api/delete` `{filename}` → removes image + thumbnail; filename
  validated against the camera dirs (no path traversal)
- `GET/POST /api/settings` → restricted to `MUTABLE_SETTINGS`
  (currently `fast_pass_engine`, `deep_backfill`) with value validation
- `GET /api/integrations` → **redacted** integration config (presence of
  tokens, never their values); `POST /api/integrations` → merge into
  `integrations.json` (blank token fields preserve the stored secret);
  `POST /api/integrations/test` → send a Slack test message. See
  [Integrations](#integrations).
CORS is open; the UI computes the API origin as
`http://<page-hostname>:8190`. Restart after editing:
`sudo systemctl restart webcam-api`.

### index.html (single-file SPA)
No build step. Key state lives in the `state` object; persisted bits in
localStorage: `webcam_ai_blacklist` (hidden labels) and
`webcam_ai_aliases` (label merging, default `{face:person, body:person}`).
- Label pipeline: raw analysis keys -> `canonicalLabel()` (alias map) ->
  blacklist filter -> filter buttons / badges / chart highlighting.
- Tabs: Objects (detections only), All, Timeline (events).
- Timeline: `computeVisits()` groups each label's contiguous presence
  into visits (start/end, duration, frame count, ongoing flag, first
  Gemma caption) from chronological verdicts; single-frame flickers
  smoothed; each visit's frame run (2 context frames + up to 60) plays
  via `openEventPlayer()` (2.5fps thumbnail flipbook).
- Captions: Gemma's optional `description` string per entry (non-empty
  scenes only); shown on cards/lightbox/visits and searchable. It's not
  a boolean key so `effectiveLabels`/`labelStates`/consensus ignore it.
- Charts: day-planner (per-day 24h timelines, red marks at match
  time-of-day) + hourly histogram (red overlay = matching share).
- Grid lazy-loads `thumbs/<file>` with onerror fallback to full res;
  lightbox and downloads always use full res.
- Filenames are parsed as AEST (+10:00) and displayed in
  Australia/Sydney. If the cameras follow DST this is off by 1h in
  summer (parse offset is fixed).

## Data files (all in repo dir, synced to web roots)

| File | Writer | Purpose |
|------|--------|---------|
| `analysis.json` | pipeline | per-image verdicts (see states below) |
| `bursts.json` | pipeline | burst id -> {summary, images[]} |
| `pins.json` | api_server | filenames protected from retention |
| `images.json` | create-index.sh | per-camera newest-first listing (web root only) |
| `settings.json` | human or api_server | pipeline tunables |

`analysis.json` entry states:
- `{"fast_pass": "negative"}` — detector saw nothing; LLM hasn't looked.
- `{labels..., "fast_pass": "partial"}` — detector hit, awaiting LLM
  (UI: "Unverified" / `label?` badges).
- `{labels...}` (no `fast_pass` key) — LLM verdict, final.
  All-false = verified clear.

Flipping a verified entry back to `"fast_pass": "partial"` re-queues it
for priority LLM re-scan (used for the one-off re-scan of mislabeled
images; partials precede backfill in the queue).

## settings.json reference

| Key | Default | Meaning |
|-----|---------|---------|
| `burst_threshold_seconds` | 300 | max gap between burst frames |
| `max_age_days` | 30 | retention: age limit for no-detection images |
| `max_dir_gb` | 6.0 | retention: per-camera disk budget |
| `min_mem_for_local_gb` | 6.0 | min free RAM to attempt local LLM |
| `allow_cloud` | false | permit OpenRouter fallback |
| `ollama_url` | http://localhost:11434 | local LLM endpoint |
| `model_local` | gemma4:12b | Ollama model tag (NOT "gemma-4:12b") |
| `max_deep_passes` | 4 | LLM calls per camera per sweep (local+cloud) |
| `fast_pass_engine` | yolo | `yolo` or `haar` (UI-selectable) |
| `deep_backfill` | true | idle LLM verification of the archive (UI-toggleable) |
| `gate_ignore_labels` | ["car"] | labels that alone don't trigger urgent deep passes |

## Integrations

The pipeline fans **burst (sequence) summaries** out to external services.
A burst summary is the gallery's "contextual analysis" — a narrative of
what happened across a run of frames — so it's the natural notification
event (per-image captions are far too frequent). The hook fires once per
burst, right after `bursts.json` is updated (`analyze_images.py`, burst
analysis step).

```
analyze_images.py  (new burst summary)
        │  notify_burst(burst_id, summary, frame_paths, image_dir)
        ▼
integrations/__init__.py   reads integrations.json, dispatches to each
        │                  enabled integration; fully guarded (a failing
        │                  notifier never escapes into the locked sweep)
        ├─ integrations/slack.py    builds a clip + posts via Slack
        └─ integrations/media.py    frames → looping GIF, MP4 fallback
```

### The contract (for new integrations)
Expose `post_burst(cfg, burst_id, summary, frame_paths)` from a module in
`integrations/`, returning `(ok, detail)` and **never raising**; then add
an `enabled` dispatch block in `notify_burst`. `frame_paths` are local
thumbnail paths (full-res fallback) for the burst's frames; `cfg` is that
integration's block of `integrations.json`. **New integrations are welcome
but must come in via PR** — keep the guard discipline (no exception may
reach the pipeline, which holds the global lock) and the secret-handling
rules below.

### integrations.json (secrets — NOT a synced data file)
Lives at the repo root, **gitignored**, written `0600` by `api_server.py`,
and deliberately **excluded from the nginx web-root sync** (`create-index.sh`
copies only `index.html` + the four public JSON files), so bot tokens are
never world-readable. `GET /api/integrations` only ever returns a redacted
view. Schema:

```json
{
  "slack": {
    "enabled": true,
    "bot_token": "xoxb-…",   "app_token": "xapp-…",
    "channel_id": "C0123ABCD",
    "public_base_url": "https://dogcam.example.org"
  }
}
```

### Slack
Posts the burst's frame animation + AI summary with a deep link back to
the gallery. Uses Slack's current upload flow (`files.getUploadURLExternal`
→ upload → `files.completeUploadExternal`; legacy `files.upload` is
retired). Needs the **bot token** (`xoxb-`, scopes `chat:write` +
`files:write`) and a **channel id**; the **app token** (`xapp-`) is stored
for future Socket Mode work but isn't required to post. `media.py` makes a
looping GIF (≤24 frames, 480px, 2.5fps) and falls back to MP4 (ffmpeg) when
the GIF exceeds 3 MB. The deep link is `<public_base_url>/?event=<burst_id>`,
which the SPA honours on load (`maybeOpenDeepLink`) by opening that
sequence's flipbook. `public_base_url` is a single site, so for a
multi-camera deployment it points at one camera's gallery — per-camera URL
mapping is a future enhancement.

## Services & infrastructure

Box-specific: paths, units, hosting, Ollama/YOLO install locations, and
operational history all live in [DEPLOYMENT.md](DEPLOYMENT.md). The app
expects only: image dirs (`watch_dirs`), a static web server per dir,
something invoking `create-index.sh <dir>` per camera (inotify
recommended, polling fallback), and an Ollama endpoint (`ollama_url`).

## Deployment

```bash
# UI changes take effect immediately:
cp index.html /mnt/models/Webcam21/index.html
cp index.html /mnt/models/Webcam22/index.html
# (create-index.sh also re-syncs it every sweep)

# Pipeline changes: nothing to do - each sweep runs the script fresh.
# API changes: sudo systemctl restart webcam-api
# Unit changes: sudo bash systemd/install.sh
```

## Troubleshooting

- **Gallery empty in Objects tab** — usually hidden labels (localStorage
  blacklist) or no verified detections for the filter; the empty state
  offers "Unhide all labels" / "Show all images".
- **Images vanishing** — grep journals for `Retention: removed` and
  `Pruned`; if absent, check root's crontab and `/etc/cron.d/`.
- **No deep passes happening** — `curl localhost:11434/api/version`
  (Ollama up?), check journal `System Check` line for
  `Local LLM Enabled: True`, and free RAM vs `min_mem_for_local_gb`.
- **Pin/delete/settings failing in UI** — `webcam-api` down or phone
  can't reach `:8190`.
- **False bursts spanning days** — the chronological sort in
  analyze_images.py was removed at some point; see step 3 above.
- Logs: `journalctl -u webcam-pipeline@Webcam21 -f` (and `@Webcam22`,
  `webcam-api`, `ollama`).
