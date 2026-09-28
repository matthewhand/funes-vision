# Developer & Operations Guide

Architecture, data formats, configuration, and operational detail for the
funes-vision. For end-user usage see [docs/USER-GUIDE.md](docs/USER-GUIDE.md)
and [README.md](README.md).

## System overview

```
Hikvision cameras ──FTP──> /mnt/models/Webcam21 (webcam)   nginx :8180 (ro)
                           /mnt/models/Webcam22 (dogcam)   nginx :8180 /Webcam22/ (ro)
                                │
                  inotifywait (create-index.sh, one per camera, systemd)
                                │  + 60s idle loop
                                ▼
                       analyze_images.py  (global flock, one at a time)
                        │ 1. retention (age + disk budget, pin-aware)
                        │ 2. thumbnails (320px JPEG -> thumbs/)
                        │ 3. fast pass: YOLO (yolov4-tiny) or Haar
                        │ 4. deep pass: gemma4:e2b via local Ollama
                        │    (HA flags; YOLO labels kept; OpenRouter if allow_cloud)
                        │ 5. burst detection + optional LLM burst summaries
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

**Host note (follow-up):** Live gallery is nginx `:8180` with Webcam21 at `/` and Webcam22 at `/Webcam22/` (repo `nginx.conf` + `docker-compose.yml` publish only `8180:80`). Host `/etc/nginx/sites-enabled/dogcam.conf` still `proxy_pass`es to `localhost:8280` where nothing listens — document-only mismatch for a later host fix. Do not commit host secrets or `.htpasswd`.

Hosting: one stock-nginx docker service (`docker-compose.yml`) with
read-only bind mounts. Deploying UI changes = copy `index.html` into both
web roots (see Deployment). The host paths are owned by the `hikvision`
FTP user but world-writable, so the pipeline (user `user`) can write.

## Components

### create-index.sh `<IMAGE_DIR>`
Watcher + orchestrator, one instance per camera (`webcam-pipeline@.service`).
- Regenerates `images.json` (newest-first file listing).
- Sources `~/.litellm/.env` for `OPENROUTER_API_KEY` (cloud fallback only).
- Runs `analyze_images.py`, then syncs `index.html`, the PWA assets
  (`manifest.json`, `icon.svg`, `favicon.ico`, pinned `lucide.min.js`),
  and JSON artifacts into the camera web root.
- Triggers: startup, every new image (inotify, 5s debounce), and a 60s
  idle loop ("catch-up sweep").
- **Global lock** `/tmp/webcam_analysis.lock` (flock, 1h timeout): the
  Python script scans BOTH camera dirs and writes shared JSON, so two
  instances must never run it concurrently. Do not make this per-camera.
- **Success marker** `/tmp/webcam_analysis.lastrun` is touched only when
  `analyze_images.py` exits 0. A failed Python run still attempts web-root
  sync (so a recovered catalog can propagate) but does **not** advance
  lastrun — `/api/health` `recent_sweep` and the cron watchdog key off that
  mtime.

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
   COCO classes mapped to person/car/bird/cat/dog, conf 0.45, ~0.2s/image.
   Haar = legacy frontal-face/fullbody/frontalcatface cascades.
5. **Deep pass** — the `model_primary` vision model through Ollama
   (`analyze_image_local`, /api/chat with `format: json` + the front/back HA
   schema). Live tag is `gemma4:e2b`. `model_primary` can be a local tag *or*
   an Ollama `:cloud` model (e.g. `minimax-m3:cloud`) — same `:11434` path
   either way. OpenRouter remains a separate fallback when `allow_cloud` is
   true. Budgeted: `max_deep_passes` per camera per sweep counts local AND
   cloud calls. **The LLM writes HA flags** (`postal_delivery`, `porch_access`,
   `animal_detected`, …). **`merge_llm_into_fastpass` never overwrites
   detector labels** (`person`/`car`/`dog`/…). There is no free-text
   `description` field.
   - *Which models run here* (`runnable_chain`): the primary→fallback chain is
     filtered to what THIS host can serve — a `:cloud` model only when
     `allow_cloud` is true (it runs on Ollama's servers, no local RAM, but
     ships frames off-box), a local model only when free RAM ≥
     `min_mem_for_local_gb`. This is why a low-RAM box still runs its cloud
     primary instead of silently doing zero deep passes (the gate used to key on
     a local-RAM threshold alone). `model_is_cloud(tag)` = `:cloud` suffix.
   - *Rate limiting*: every Ollama chat call goes through `_ollama_chat`, which
     retries HTTP 429/503 with **exponential backoff + jitter** (`backoff_delay`,
     capped at 30 s, honoring `Retry-After`). If still throttled after
     `LLM_MAX_RETRIES`, it raises `RateLimited`, which sets a per-sweep
     `RATE_LIMITED` flag — `run_deep_pass` and burst analysis then **stop for the
     rest of that sweep** and resume on the next one (~60 s later). Cloud models
     therefore never get hammered through a backlog.
   - *Urgency gate*: a fast-pass hit consisting only of
     `gate_ignore_labels` (default `["car"]` — a car parked in frame 24/7)
     is stored as detector labels plus `_llm_skip: no_trigger`. It is
     **not** marked `partial` (that would re-enter the priority queue) and
     does not consume urgent budget. `/api/status` counts these under
     `awaiting_backfill`.
6. **Idle backfill** (`deep_backfill`) — leftover budget verifies
   fast-pass negatives, `no_trigger` skips, and ignore-only partials, closest-to-a-detection
   first then newest, so the archive converges on HA flags. **Off on this
   box** (`deep_backfill: false`). Fanned out
   across `deep_concurrency` workers (`concurrency_workers` clamps to
   `[1, len(targets)]`); results are consumed in the main thread so
   `analysis_data` is never mutated concurrently, and a peer tripping
   `RATE_LIMITED` short-circuits the rest. The audit-trail files
   (`inference_status.json`/`inference_log.json`) are written via
   `_atomic_write_json` under `_IO_LOCK` so concurrent passes and the live
   API reader never see a half-written file.
7. **Bursts** — consecutive images < `burst_threshold_seconds` apart form
   a burst. If `burst_summaries_enabled` (default **off**, UI-toggleable),
   bursts containing a detection get an LLM sequence summary (last 3
   frames). Invalid cached bursts (missing files, or spans violating the
   chain rule) are pruned and re-detected. A new summary is fanned out to
   enabled notifiers — see [Integrations](#integrations).
8. **Pruning** — analysis/burst entries whose files no longer exist
   (retention, API delete, external cleanup) are removed.

Inference timing on this box (4-core ARM, no GPU): live model is local
**`gemma4:e2b` at ~40 s/image** (recent priority passes cluster around 42–44 s;
occasional outliers are much slower). `allow_cloud` is false; the deep pass
does **not** run `gemma4:12b` (~5.5 min/image) or a cloud primary here.
`max_deep_passes` (30) bounds a sweep so it can't hold the 1h lock indefinitely.
Idle backfill is off, so the archive is not grinding toward full HA coverage.

### Retention (file rotation)

Retention is the app’s **file rotation** — not classic log rotate, and not
a blind `find -mtime | rm`. It runs **first** in every full sweep (and on
demand via `--retention-only` / the cron watchdog) under the global flock.

#### Configuration (`settings.json`)

| Key | Live default | Meaning |
|-----|--------------|---------|
| `max_age_days` | 30 | Age pass: delete unpinned **non-timeline** images older than this |
| `max_dir_gb` | 5.0 | Per-camera dir budget (sum of image file sizes **plus matching thumbs**) |
| `persist_budget_pct` | 20 | Ceiling: age-expired **LLM-verified timeline** frames may use at most this % of `max_dir_gb` (1.0 GiB when the dir budget is 5.0). Not a reservation — the rolling window always gets the rest so new detections cannot fill the dir to 100%. |
| `pins.json` | `[]` | Filenames never deleted by retention |

These knobs are **not** in the UI/`POST /api/settings` allow-list — edit
`settings.json` on disk (or extend `MUTABLE_SETTINGS` if you want them
API-writable). Code fallbacks if the keys are missing: `max_age_days=30`,
`max_dir_gb=5.0`, `persist_budget_pct=20`.

**Persistable** (may outlive `max_age_days`) means `catalog.is_timeline_persistable`:
a successful LLM merge (`_llm` dict, no skip) **and** either a True HA
activity flag (`porch_access`, `animal_detected`, `postal_delivery`, …) or
a detector label that is not gate-ignored (person/dog/cat/bird, not
car-only). Motion JPEGs and YOLO-only hits are not persistable.

#### Passes (`apply_retention`)

For each path in `watch_dirs`, after listing image files (`.jpg/.jpeg/.png/.gif`):

1. **Age** — delete if mtime &lt; now − `max_age_days` **and** not pinned
   **and** not persistable. YOLO-only parked-car/face shots rotate; LLM
   timeline visits stay.
2. **Persist cap** — among remaining persistable frames older than
   `max_age_days`, if their charged bytes &gt; `persist_budget_pct` of
   `max_dir_gb`, delete oldest until under that ceiling.
3. **Disk budget** — if image+thumb bytes still &gt; `max_dir_gb` GiB:
   delete oldest **negatives** (including car-only), then oldest **YOLO-only**
   detections, then oldest **persistable**, then oldest **unanalyzed**.
   Pins remain sacred. Unanalyzed frames newer than `max_age_days` are
   otherwise kept so they can be reviewed first.

Thumbnails at `<dir>/thumbs/<filename>` are removed with each parent image
and **count toward** `max_dir_gb`. Thumbs whose parent image is already
gone (legacy deletes) are removed as a final sweep.

**Never deleted by retention:** pinned filenames.  
**Not counted toward `max_dir_gb`:** JSON catalogs, copied gallery HTML/guide
assets, host free space on `/` or the whole `/mnt/models` volume
(Ollama/models/projects sit outside the per-camera budget — see
[DEPLOYMENT.md](DEPLOYMENT.md)).

#### When it runs

| Path | When |
|------|------|
| Full sweep | Every `analyze_images.py` run (inotify / idle / startup), step 1 |
| `--retention-only` | Cron hourly + manual; age/budget + catalog prune only — **no** YOLO/LLM queue |
| `--rescan-days N` | One-shot: re-YOLO + current e2b scans on person/dog/cat/bird frames in the last N days. Empties and car-only skipped. Holds the pipeline flock |
| Manual API delete | `POST /api/delete` (also unpins); not retention, but same disk effect |

After deletes, the sweep **prunes** stale keys from `analysis.json` /
`bursts.json` (files gone → entries removed) and appends to
`retention_log.json` (last 100 events: `ts`, `dir`, `count`, `bytes_freed`).
The UI status panel shows the latest event; `GET /api/status` exposes
`retention`.

#### Catalog durability (why retention used to die)

`analysis.json` is shared across cameras and required for pin-aware rules.
A historical failure mode:

1. Non-atomic write (`open(w)` + `json.dump`) during **ENOSPC** truncated the
   file mid-key.
2. Next sweep did bare `json.load` → **uncaught** `JSONDecodeError`.
3. Process exited **before** `apply_retention` → no cleanup → disks kept
   growing → more write failures.

Mitigations now in code:

- **`load_json_file`** — on parse error, `_recover_truncated_json` drops an
  incomplete trailing entry, closes the root object, rewrites a clean file
  via `_atomic_write_json`, and continues. Missing/unrecoverable → empty
  dict (retention still runs; budget escape can clear unanalyzed backlog).
- **`_atomic_write_json`** — temp file + `fsync` + `os.replace` for
  `analysis.json`, `bursts.json`, `retention_log.json`, `alert_state.json`
  (same pattern already used for inference audit files).
- **create-index.sh** — does not stamp `lastrun` on Python failure.
- **`load_settings(strict=True)`** — a `settings.json` that cannot be read or
  parsed raises `SettingsError`, and `main()` returns **exit 3** before taking
  the lock, before `apply_settings`, and before touching any catalog. A
  *missing* file (fresh install) and a valid object with no `watch_dirs` are
  still the benign exit 0. The old behaviour degraded a corrupt file to `{}`,
  which looked identical to "unconfigured": nothing swept, nothing deleted,
  no health alert, exit 0, `lastrun` stamped — dead retention on a box the
  watchdog read as healthy. Journal line to grep:
  `ERROR … could not read settings …`.

Journal lines to grep: `Retention: removed`, `Recovered … analysis.json`,
`Warning: … is corrupt`, `Pruned N stale analysis entries`.

#### CLI

```bash
# Full sweep (retention + analysis) — usually run by create-index.sh
python3 analyze_images.py

# Cleanup only (used by tools/watchdog.sh retention)
python3 analyze_images.py --retention-only
```

> **History:** legacy `find … -mtime +10 -exec rm` in *root’s* crontab and
> `/etc/cron.d/server-maintenance` deleted images — including verified
> person shots — behind this system. Removed 2026-06-12. If images vanish
> without `Retention: removed` / `Pruned` log lines, check other crontabs
> first. Do **not** reintroduce blind find/rm for camera dirs.

### Cron watchdog (`tools/watchdog.sh`)

Systemd keeps the long-lived pipeline up, but a unit can stay **active**
while a sweep is stuck for hours (global flock held by a multi-hour analyze,
corrupt catalog that used to abort before retention, etc.). The watchdog is
an **independent cron safety net** so cleanup and liveness do not depend
solely on those processes remaining healthy.

#### Install & schedule

Installed by `sudo bash systemd/install.sh` as
`/etc/cron.d/webcam-watchdog` (user **user**):

| Cron | Mode | Behaviour |
|------|------|-----------|
| `*/15 * * * *` | `check` | Ensure units active; restart if last successful sweep older than 2h (anti-thrash) |
| `5 * * * *` | `retention` | Always run app-configured cleanup (`--retention-only`) |

Manual:

```bash
tools/watchdog.sh check        # units + stale-sweep only
tools/watchdog.sh retention    # force retention-only under the flock
tools/watchdog.sh auto         # check, then retention if thresholds say so
tools/watchdog.sh help
```

Log: `watchdog.log` in the app dir (gitignored `*.log`) and syslog tag
`webcam-watchdog`.

#### What `check` does

1. `systemctl is-active` for `webcam-pipeline@Webcam21`,
   `@Webcam22`, `webcam-api` — restart any that are not `active`
   (`sudo -n systemctl restart …` when passwordless sudo is available).
2. `GET /api/health` and `GET /api/status` (note: health may return **503**
   when degraded; the client must **not** use `curl -f`, or the body is
   discarded).
3. If the API is unreachable → restart `webcam-api` and re-probe.
4. If last successful sweep age ≥ `WATCHDOG_STALE_S` (default **7200** s) →
   restart both pipeline units, but at most once per that window
   (`/tmp/webcam_watchdog_last_restart`) so a stuck `lastrun` does not
   thrash restarts every 15 minutes.
5. Logs a one-line summary: health, sweep age, max camera `budget_pct`,
   free GB, whether restart/retention are indicated.

#### What `retention` does

1. If `/tmp/webcam_analysis.lock` is held (usually a long analyze), stop
   in-flight `analyze_images.py` and free lock holders with `fuser -k` on
   the lock file (**from outside** any open fd on that path — killing from
   inside the flock subshell would kill the watchdog itself).
2. Acquire the same global flock, run
   `python3 analyze_images.py --retention-only`.
3. On success: touch `/tmp/webcam_analysis.lastrun`, copy
   `analysis.json` / `bursts.json` / `pins.json` into each `watch_dirs` web
   root.

This uses **only** the app’s retention rules (pins, age, budget) — never a
raw `find` wipe.

#### What `auto` does

Runs `check`, then runs `retention` if any of:

- any camera `budget_pct` ≥ `WATCHDOG_BUDGET_PCT` (default **90**)
- host free space &lt; 1 GB (from `/api/status` filesystem)
- sweep considered stale / restart indicated
- `recent_sweep` false and sweep age &gt; 1 h
- last `retention_log` event older than 1 h (or missing)

The hourly cron already forces retention; `auto` is for ad-hoc “fix if
needed” runs.

#### Environment overrides

| Env | Default | Meaning |
|-----|---------|---------|
| `WATCHDOG_API` | `http://127.0.0.1:8190` | Status/health base URL |
| `WATCHDOG_STALE_S` | `7200` | Restart pipelines if lastrun older than this (seconds) |
| `WATCHDOG_BUDGET_PCT` | `90` | `auto` retention kick threshold |
| `WATCHDOG_LOG` | `$BASE/watchdog.log` | Log path |
| `WATCHDOG_LOCK` / `WATCHDOG_MARKER` | `/tmp/webcam_analysis.{lock,lastrun}` | Flock + success marker |
| `WATCHDOG_RESTART_STATE` | `/tmp/webcam_watchdog_last_restart` | Restart-loop backoff stamp; the `Restarting` troubleshooting entry below is the symptom of a full one |

#### Operational notes

- The watchdog does **not** replace systemd; it complements it.
- Restart requires passwordless sudo for `systemctl` (this box grants it to
  `user`). Without sudo, unit restarts are logged as errors; retention
  still runs as the same user as the pipeline.
- After a lock steal, create-index’s long-lived inotify parent stays up;
  idle/inotify will start a fresh analyze on the next trigger.
- Box-specific install paths and disk layout:
  [DEPLOYMENT.md](DEPLOYMENT.md#cron-watchdog-safety-net).

### api_server.py (port 8190)
Stdlib-only HTTP server, the single write channel (nginx mounts are ro). It
exposes 11 routes — `pins`, `pin`, `delete`, `settings` (GET/POST), `integrations`
(GET/POST + `/test`), `status`, `health`, `inference_log`, `POST /api/clip`
(GIF for the player; MP4 accepted by the API only), `GET /api/llm-schema`
(live prompt + front/back HA schemas), and the `events` SSE stream
(`image.new` / `detection.preliminary` / `new-detection` / `new-burst`;
`X-Accel-Buffering: no` so it survives the proxy unbuffered).

**Methods, request/response payloads, status codes, and the SSE event schema
are documented once in [API.md](API.md)** — the source of truth; keep it in
sync with the code. Two things worth repeating here: the only settings
`POST /api/settings` will accept are `fast_pass_engine`, `deep_backfill`,
`deep_passes_enabled`, `burst_summaries_enabled`, `idle_sweep_seconds`; and `GET /api/integrations` is
always **redacted** (token presence, never values — see [Integrations](#integrations)).

API origin: the UI calls the API **same-origin at `/api/`** so there's no
second port to expose publicly — the reverse proxy maps `/api/` to
`localhost:8190` behind its basic-auth (see Deployment), falling back to
`http://<host>:8190` only when a page is opened **directly** on a camera
container (`:8180`, i.e. LAN/dev; legacy `:8280` unused). Restart after editing:
`sudo systemctl restart webcam-api`.

#### Environment overrides

The API's whole environment surface, defaults cross-checked against
`api_server.py`. The `Read` column is the one that bites: everything except
`WEBCAM_API_HOST` is read **per request / per call**, so an edit to the systemd
`EnvironmentFile` applies on the next request with no restart — while
`WEBCAM_API_HOST` is bound at import and needs `systemctl restart webcam-api`.

| Env | Default | Read | Meaning |
|-----|---------|------|---------|
| `WEBCAM_API_SOCKET_TIMEOUT` | `30` (s) | per connection | Read timeout on one client socket. A stalled peer can't park a handler thread and starve `/api/health`. Raise only for a genuinely slow client. |
| `WEBCAM_API_MAX_BODY` | `1048576` (1 MiB) | per `POST` | Largest accepted `Content-Length`; over it (or negative) is **413** `request body too large`, refused before the body is read. Every mutating endpoint is small JSON — leave it alone. |
| `WEBCAM_PROBE_TTL` | `5` (s) | per probe call | Memoises the `pgrep` inotify probe and the Ollama `/api/version` reachability behind `/api/status` and `/api/health`, so 20 polls cost 2 forks instead of 20. `0` re-probes every call. Raise only if you need fresher `llm.reachable` than the default ~5 s. |
| `WEBCAM_API_HOST` | `127.0.0.1` | **once at startup** | Bind address — the only exposure control. `0.0.0.0` only behind a proxy that does its own auth; restart the unit after changing. |
| `WEBCAM_API_TOKEN` | unset (auth off) | per request | Shared secret gating **every** `POST`. WINS over the `api_token` key in `settings.json`. A blank env value falls through to `settings.json`; an `api_token` key that is present but blank is a misconfiguration the server **refuses to start on** (`auth=broken`) rather than silently serving auth-off. |
| `WEBCAM_CORS_ORIGIN` | `http://localhost:8180,http://127.0.0.1:8180` | per request | Comma-separated origin allowlist. A literal `*` is dropped and never sent — the wildcard is the hole this replaced. Add your real origin when the gallery is served from another host or port. |
| `WEBCAM_SSE_MAX_CLIENTS` | `8` | per connection | Concurrent `/api/events` cap (`0` disables); past it a new stream gets **503**, not another thread. Raise if several tabs or reverse proxies are legitimate clients. |
| `WEBCAM_SSE_HEARTBEAT_S` | `15` (s) | per stream | `event: ping` cadence (floored at 1 s) — a bare `: ping` is invisible to `EventSource`, so the client can see a silently stalled connection. |
| `WEBCAM_SSE_IDLE_TIMEOUT_S` | `600` (s) | per stream | Reap a stream that has seen no real event for this long (`event: close`); `0` disables. |
| `WEBCAM_TZ` | settings.json `timezone` > `Australia/Sydney` | per `/api/status` | Display timezone for the UI. Pin it with `Environment=WEBCAM_TZ=...` in the `webcam-api` unit (see [Timezones](#indexhtml-single-file-spa)) so filenames (SOURCE_TZ) and the display agree year-round. |

The first three are the per-request limits from #41; the HTTP contract they
implement (including the **413**) is in
[API.md — Request limits](API.md#request-limits). Two more knobs live outside
this section: `WEBCAM_LOG_LEVEL` (default `INFO`; unknown values fall back to
`INFO`) is process-wide and applies to the pipeline too, and the host/systemd
variables (`WEBCAM_USER`, `WEBCAM_DIR`, `WEBCAM_CAMERAS`, `OLLAMA_*`, …) are
tabulated in [DEPLOYMENT.md](DEPLOYMENT.md#host-configuration-etcwebcamwebcamenv)
— they are read by `systemd/install.sh` and the units, not by the API.

### index.html (single-file SPA)
No build step. Key state lives in the `state` object; persisted bits in
localStorage: `webcam_ai_blacklist` (hidden labels) and
`webcam_ai_aliases` (label merging, default `{face:person, body:person}`).
- Label pipeline: raw analysis keys -> `canonicalLabel()` (alias map) ->
  blacklist filter -> filter buttons / badges / chart highlighting.
- Tabs: Objects (detections only), All, Timeline (events).
- Timeline: `computeVisits()` groups each label's contiguous presence
  into visits (start/end, duration, frame count, ongoing flag) from
  chronological detector (+ leftover `description`) records; single-frame
  flickers smoothed; each visit's frame run (2 context frames + up to 60)
  plays via `openEventPlayer()` (2.5fps thumbnail flipbook). Player
  download is **GIF only**.
- Captions: the UI still reads `analysis.description`. The e2b deep pass
  **does not write that field** — it writes HA flags instead (shown as
  badges / ℹ schema). Old catalog rows may still have a caption.
- Charts: day-planner (per-day 24h timelines, red marks at match
  time-of-day) + hourly histogram (red overlay = matching share).
- Grid (and Timeline visit thumbs) lazy-load `thumbs/<file>` via
  IntersectionObserver. A missing thumb shows the unavailable glyph —
  it does **not** pull the full JPEG into the grid. Lightbox / download
  still use full res. `lucide.min.js` loads at the end of `<body>`.
  `images.json` paints the date rail before `analysis.json` is parsed.
  Visit grouping runs only on the selected day (default today), not the
  whole archive. Activity charts wait for idle. Catalogs revalidate with
  `cache: 'no-cache'` (no `?t=` bust); `create-index.sh` slices
  `analysis.json` / `bursts.json` / `pins.json` to that camera's
  `images.json`.
- Timezones: filenames carry the camera's **local wall-clock** time.
  `SOURCE_TZ` (the camera location, `Australia/Sydney`) is the zone those
  digits are in; `zonedTimeToUtc()` resolves the true instant **DST-aware**
  (AEDT +11 Oct–Apr, AEST +10 Apr–Oct), so there is no longer a fixed-offset
  summer drift. Display happens in `DISPLAY_TZ`, resolved from `/api/status`
  → `WEBCAM_TZ` env > settings.json `timezone` > `Australia/Sydney`
  (`resolveDisplayTz()`). With the default, displayed time equals the filename
  digits year-round. Date grouping (`dateStr`) still uses the raw filename
  digits, i.e. always SOURCE_TZ. To pin the zone on the API service, set
  `WEBCAM_TZ=Australia/Sydney` in the `webcam-api` systemd unit
  (`Environment=WEBCAM_TZ=Australia/Sydney`); until then the settings.json
  default applies.

**UI interactions (keyboard / gestures / live):**
- Lightbox: `←`/`→` navigate, `Esc` close, `Space` slideshow, `+`/`-` zoom,
  scroll-wheel zoom, drag to pan when zoomed, **swipe** to navigate on
  mobile (only when not zoomed); neighbours are preloaded for instant nav.
- Flipbook player (`openEventPlayer`): a frame **scrubber** plus `←`/`→`
  step, `Space` play/pause, `Esc` close; "Copy link" yields a `?image=`
  deep link.
- Real-time: an `EventSource` on `/api/events` raises an accumulating
  **activity pill** ("N new — tap to view") and a `(N)` **tab-title badge**;
  tapping the pill clears both and scrolls to newest. Falls back to polling.
- Deep links honoured on load (`maybeOpenDeepLink`): `?event=<burst_id>`
  (sequence) and `?image=<filename>` (single frame); a copy-link button in
  the player and lightbox produces them.
- A back-to-top button appears after scrolling ~600px.

## index.html layout

No build step: the SPA is one file with one inline `<script>` at the end of
`<body>` (`lucide.min.js` is the only external script). The inline logic is
split into banner-delimited sections — router, state, pure helpers, rendering,
live/SSE, bootstrap/events — summarised in the **section map** at the top of
`index.html` (an HTML comment block, so it costs nothing at runtime).

**Sentinel contract.** Pure, side-effect-free helpers are wrapped in a marker
pair:

```js
// === pure:NAME ===
function NAME(...) { ... }
// === /pure:NAME ===
```

`tests/*.js` extracts that exact block and `eval`s it, so the page and the
unit test share **one** copy of the function. The pair is machine-checked in
two places:

- `tests/test_sentinel_inventory.py` — every `pure:NAME` referenced by a test
  must have both markers in `index.html`; markers must not be duplicated,
  unmatched, or left with an empty body.
- `bash tests/run.sh` — extracts the inline script with
  `tools/lint/extract-inline.mjs` and runs `node --check` on it, so a syntax
  error fails locally instead of only in a browser.

When you add, rename, or move a helper, update its sentinel pair, the section
map at the top of `index.html`, and (if new) the "Covered today" list under
[Testing](#testing).

## Data files (all in repo dir, synced to web roots)

| File | Writer | Purpose |
|------|--------|---------|
| `analysis.json` | pipeline | per-image verdicts (see states below) |
| `bursts.json` | pipeline | burst id -> {summary, images[]} |
| `pins.json` | api_server | filenames protected from retention |
| `images.json` | create-index.sh | per-camera newest-first listing (web root only) |
| `settings.json` | human or api_server | pipeline tunables |
| `inference_log.json` | pipeline | LLM audit trail (served via `/api/inference_log`); gitignored, local-only |
| `inference_status.json` | pipeline | live "analyzing now" state; gitignored, local-only |
| `retention_log.json` | pipeline | deletion audit (count + bytes); gitignored, local-only |
| `integrations_state.json` | pipeline | last Slack delivery result; gitignored, local-only |
| `alert_state.json` | pipeline | health-alert debounce/recovery state; gitignored, local-only |
| `integrations.json` | api_server | Slack / ntfy / MQTT secrets; gitignored, mode 600 (see [Integrations](#integrations)) |

`analysis.json` entry states:
- `{"fast_pass": "negative"}` — detector saw nothing; LLM hasn't looked.
- `{labels..., "fast_pass": "partial"}` — detector hit, awaiting LLM
  (UI: "Unverified" / `label?` badges), or a skipped/failed deep pass
  (`_llm_skip` other than `no_trigger`).
- `{car: true, "_llm_skip": "no_trigger"}` — ignore-only detector hit
  (default: car). Not `partial` (would re-queue as urgent). Not a verdict.
- Successful merge: detector labels + HA flags, a dict `_llm`, no
  `_llm_skip`. `fast_pass` is dropped. YOLO `person`/`car`/`dog`/… **kept**;
  flags such as `postal_delivery` / `porch_access` / `animal_detected`
  attached. The LLM does **not** replace the detector record wholesale.
  **This is the only `llm_verified` / SSE `new-detection` shape.** Missing
  `fast_pass` alone is not a verdict — bare `{person: true}` and car-only
  `no_trigger` rows are not counted.

`GET /api/status` `queue` (`queue_from_analysis` / `is_llm_verified` /
`is_awaiting_backfill`):
- `unverified_partials` — `fast_pass == "partial"`
- `awaiting_backfill` — negatives **plus** `_llm_skip == "no_trigger"`,
  even when `deep_backfill` is off, so the count stays honest
- `llm_verified` — `_llm` is a dict and `_llm_skip` is absent

SSE `new-detection` uses the same `is_llm_verified` gate
(`_verified_detections`). `detection.preliminary` is detector-only
(`fast_pass` present, or `_llm_skip == "no_trigger"` with a YOLO True
key) and is disjoint from verified.

Flipping a verified entry back to `"fast_pass": "partial"` re-queues it
for priority LLM re-scan (used for the one-off re-scan of mislabeled
images; partials precede backfill in the queue).

### Paths & XDG

All paths today are computed relative to the script dir (`BASE_DIR =
dirname(__file__)`, i.e. the repo checkout) or the per-camera web roots; there
is **no** XDG Base Directory support yet. The files split into two planes:

- **Data plane (browser-facing)** — `images.json`, `analysis.json`,
  `bursts.json`, `pins.json`, `thumbs/`, and the JPEGs live in the nginx web
  roots (`/mnt/models/Webcam2{1,2}`) because the read-only nginx containers
  serve them straight to the SPA. These belong with the camera data, **not** a
  user-home dir.
- **Control plane (server-side)** — config (`settings.json`,
  `integrations.json`) and runtime state (`inference_log.json`,
  `inference_status.json`, `retention_log.json`, `alert_state.json`,
  `integrations_state.json`) currently sit in the repo dir, interleaved with
  source. Ephemeral markers (`/tmp/webcam_analysis.{lock,lastrun}`) live in `/tmp`.

**XDG (considered, not scheduled).** Adopting XDG would map cleanly onto the
*control plane only*: config → `$XDG_CONFIG_HOME/webcam/`, server-only state →
`$XDG_STATE_HOME/webcam/`, the `/tmp` markers → `$XDG_RUNTIME_DIR/webcam/`. The
data plane stays put. The main payoff is getting secrets + mutable state out of
the checkout; the cost is a shared path resolver (env override → XDG → legacy
`BASE_DIR` fallback) imported by all three entry points (`api_server.py`,
`analyze_images.py`, `integrations/__init__.py`), consistent `XDG_*`/env in the
two systemd units, and a one-time migration. `pins.json` is dual-purpose (server
state **and** a web-served copy), so it would keep a synced web-root copy.
A concrete, owner-gated migration plan (resolver → systemd env → one-time file
move → verify) lives in [ROADMAP.md](ROADMAP.md).

## settings.json reference

> **User-local file.** `settings.json` holds deployment-specific paths and camera geometry, so it is **gitignored** — every install keeps its own copy. Copy the tracked template [`settings.example.json`](settings.example.json) (generic placeholder paths, empty `ignore_regions`, documented defaults) to `settings.json` and edit it for your box. The table below lists the keys and their documented defaults.

| Key | Default | Meaning |
|-----|---------|---------|
| `burst_threshold_seconds` | 300 | max gap between burst frames |
| `idle_sweep_seconds` | 60 | idle re-scan cadence (UI-settable, 15–3600) |
| `timezone` | Australia/Sydney | display TZ; `WEBCAM_TZ` env overrides (see [Paths & XDG](#paths--xdg)) |
| `max_age_days` | 30 | retention: age limit for unpinned **non-timeline** images (not UI-mutable; edit file) |
| `max_dir_gb` | 5.0 | retention: per-camera image+thumb budget in GiB (code fallback 5.0 if the key is missing; not UI-mutable) |
| `persist_budget_pct` | 20 | retention: max % of `max_dir_gb` for LLM-verified timeline frames older than `max_age_days` (code fallback 20; not UI-mutable) |
| `min_mem_for_local_gb` | 6.0 | min free RAM to attempt a **local** model; a `:cloud` model ignores this (see `runnable_chain`) |
| `allow_cloud` | false | kill switch for ALL cloud inference: gates the OpenRouter fallback AND any Ollama `:cloud` model in the chain |
| `ollama_url` | http://localhost:11434 | local LLM endpoint |
| `ollama_keep_alive` | 24h | sent on every `/api/chat`; activity refreshes the unload timer (Ollama default is 5m). Also `OLLAMA_KEEP_ALIVE` on the ollama unit |
| `model_local` | gemma4:e2b | Ollama model tag (back-compat default for `model_primary`; import-time fallback in code is also `gemma4:e2b`) |
| `model_primary` | gemma4:e2b | primary inference model via Ollama (local tag or a `:cloud` model) |
| `model_fallback` | "" | optional fallback tried when the primary errors/rate-limits (import-time default is empty) |
| `max_deep_passes` | 30 | LLM calls per camera per sweep (local+cloud; import-time fallback is 30) |
| `deep_concurrency` | 1 | parallel **backfill** deep passes. Safe only with a `:cloud` model (no local RAM contention); the cloud endpoint partially parallelizes (~1.4× at 3). Rate-limit backoff + per-sweep `RATE_LIMITED` still guard it |
| `fast_pass_engine` | yolo | `yolo` or `haar` (UI-selectable) |
| `deep_passes_enabled` | true | master switch for ALL vision-model work (priority+backfill+bursts); false = detector-only, no LLM (UI-toggleable) |
| `deep_backfill` | false | idle LLM verification of the archive (UI-toggleable; **off** on this box) |
| `burst_summaries_enabled` | false | multi-image (burst) LLM captions of a visit; off does not affect single-frame deep passes (UI-toggleable) |
| `gate_ignore_labels` | ["car"] | labels that alone don't trigger urgent deep passes |
| `ignore_regions` | parked-car triangle + porch quad | Spatial masks. `mode=ignore`: YOLO drops a `car` whose centre sits in the parked-SUV triangle (street / leaving cars kept). `mode=gate` + `scan=porch`: person centre on the grey tiles → `porch_access` without an e2b call; path/street is false. UI: Settings → Ignore parked car / Gate porch by tiles. Applies to **new** stills |
| `camera_offline_hours` | 24 | no frames in this long → a Slack "camera offline?" alert |
| `cameras` | _(absent)_ | explicit camera registry — see [Camera registry](#camera-registry-cameras). Absent keeps the legacy Webcam21/22 + IP heuristic |
| `mqtt_topic_prefix` | funes_vision | namespace for the retained HA vision topics (`<prefix>/vision/<camera>`); `MQTT_TOPIC_PREFIX` env overrides it (see [Integrations](#integrations)) |

### Camera registry (`cameras[]`)

Camera identity is **declarative**. Each entry maps a stable `id` to a label,
a schema `kind`, and the image directory the pipeline reads:

```json
"cameras": [
  { "id": "Webcam21", "label": "Front Door", "kind": "front", "dir": "/mnt/models/Webcam21" },
  { "id": "Webcam22", "label": "Dog Cam",    "kind": "back",  "dir": "/mnt/models/Webcam22" }
]
```

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | yes | stable id used by `?camera=<id>` routing and the SPA camera switcher |
| `label` | no | human display name (defaults to `id`) |
| `kind` | no | schema axis: `front` (default) or `back`; `dog` is accepted as an alias for `back`, `other` behaves as `front` |
| `dir` (alias `source`) | yes | image directory; `camera_kind()` matches a path containing it |

`analyze_images.camera_kind()` checks the configured cameras first and uses
their `kind`; when `cameras[]` is absent (or a path matches no camera) it falls
back to the pre-abstraction heuristic (`Webcam21`/`Webcam22`,
`10.0.0.21`/`10.0.0.22`) so existing installs keep working. To adopt the
registry, add `cameras[]` mirroring your `watch_dirs`; only the fallback still
branches on the old folder names/IPs, and new deployments never hit it.

The camera stills are parsed by a **source-specific filename pattern**: the
Hikvision OSD/FTP name `10.0.0.21_01_YYYYMMDDHHMMSSmmm_MOTDEC.jpg`. It lives in
two places — `parseFilenameFields()` in `index.html` (UI date/time/event) and
`FILENAME_TS` in `ha_mqtt.py` (publish timestamps). A different camera or
recorder means editing those two regexes; the ingest contract is otherwise
"anything that writes JPEGs into a configured `dir`" (FTP/inotify).

## Observability & health alerts

`/api/status` `queue` (above) and `/api/health` are the read path — verified
counts need a successful `_llm` merge, not the absence of `fast_pass`.
Beyond those, the pipeline pushes **debounced Slack alerts** at the end of
each sweep (`run_health_checks` in
`analyze_images.py`) when: local Ollama is down with no cloud fallback, a
camera has gone silent past `camera_offline_hours`, storage exceeds 90% of
`max_dir_gb`, or ≥3 inferences failed in the last hour. State lives in
`alert_state.json` with a 6h cooldown so a persistent condition alerts once,
and a ✅ recovery is sent when it clears. Alerts go through the same
integrations layer (`notify_alert`) and only fire when Slack is enabled.
Camera-offline is intentionally conservative — motion cams are legitimately
quiet, so the default threshold is generous and tunable.

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
        ├─ integrations/slack.py    builds a clip + posts via Slack (uploads image)
        ├─ integrations/ntfy.py     pushes text + a click-through link via ntfy
        └─ integrations/media.py    frames → looping GIF, MP4 fallback
```

### The contract (for new integrations)
Expose `post_burst(cfg, burst_id, summary, frame_paths)`, `post_image(cfg,
filename, labels, caption, image_path)`, `send_message(cfg, text)` and
`send_test_message(cfg)` from a module in `integrations/`, each returning
`(ok, detail)` and **never raising**; then add the module name to the
`PROVIDERS` tuple in `integrations/__init__.py` — dispatch (and per-provider
delivery recording) is then automatic across burst/image/alert. `frame_paths`
are local thumbnail paths (full-res fallback); `cfg` is that integration's
block of `integrations.json`. **New integrations are welcome but must come in
via PR** — keep the guard discipline (no exception may reach the pipeline,
which holds the global lock) and the secret-handling rules below.

### integrations.json (secrets — NOT a synced data file)
Lives at the repo root, **gitignored**, written `0600` by `api_server.py`,
and deliberately **excluded from the nginx web-root sync** (`create-index.sh`
copies only `index.html` + the four public JSON files), so bot tokens are
never world-readable. `GET /api/integrations` only ever returns a redacted
view (Slack only). Schema:

```json
{
  "slack": {
    "enabled": true,
    "bot_token": "xoxb-…",   "app_token": "xapp-…",
    "channel_id": "C0123ABCD",
    "public_base_url": "https://dogcam.example.org",
    "notify_mode": "context"
  },
  "ntfy": {
    "enabled": false,
    "server_url": "https://ntfy.example.com",
    "topic": "my-secret-topic",
    "token": "tk_… (optional, for protected topics)",
    "public_base_url": "https://dogcam.example.org",
    "notify_mode": "context"
  },
  "mqtt": {
    "enabled": false,
    "host": "broker.internal",
    "port": 1883
  }
}
```

`POST /api/integrations` merges Slack fields only. A missing file becomes a
slack-only object (mode 600). Existing `mqtt` / `ntfy` blocks are preserved.
If the file is present but corrupt, unreadable, whitespace-only, or not a
JSON object, the handler returns **409** (`IntegrationsUnreadable`) and
**does not write** — MQTT/ntfy secrets are never truncated away. GET still
treats a corrupt file as `{}` (fail-closed read path).

ntfy is config-file-driven (no UI panel yet): it pushes the summary/caption as
the message body with a **Click** link back into the gallery (needs
`public_base_url`); unlike Slack it does **not** upload the frame. Enable by
adding the block above and setting `enabled: true`. **`server_url` is
required** (topic alone is not enough). There is no `https://ntfy.sh`
default — a missing or blank `server_url` does not publish.

Optional **HA MQTT** (`ha_mqtt.py`, `integrations.json` `mqtt` block) publishes
retained vision flags to a broker and **may leave the box**. `enabled`
defaults **false** (missing file, missing `mqtt` block, or empty block).
A broker is used only when `host` / `hosts` or `MQTT_HOST` is set — there
is no `10.0.0.111` / `127.0.0.1` fallback (`DEFAULT_HOSTS` is gone).
Enabled with an empty host list is a no-op (`no hosts`). It is not in the
Slack settings UI. `GET /api/integrations` redacts Slack only.

Env beats `integrations.json` for `MQTT_HOST`, `MQTT_PORT` (default `1883`),
`MQTT_USER`, `MQTT_PASSWORD`, `MQTT_CLIENT_ID` (default `funes-vision`), and
`MQTT_TOPIC_PREFIX` (default `funes_vision`; a leading/trailing `/` is
stripped, and it wins over the settings.json `mqtt_topic_prefix` key).

`notify_mode` (per integration) controls *what* triggers a post:
- `context` (default, quietest) — only burst/sequence summaries
  (`notify_burst`). With `burst_summaries_enabled` off this path is silent.
- `objects` — each **new urgent** detector hit (`person`/`dog`/`cat`/`bird`,
  via `maybe_notify_urgent_frame` → `notify_image`). The vision merge does
  not have to succeed. Parked-car `no_trigger` and idle backfill do not post.
- `all` — same call site as `objects` today (empties and parked cars do
  **not** post). The select label in the UI says so.

Per-image modes never fire on idle backfill.

### Slack
Posts the burst's frame animation + AI summary with a deep link back to
the gallery. Uses Slack's current upload flow (`files.getUploadURLExternal`
→ upload → `files.completeUploadExternal`; legacy `files.upload` is
retired). Needs the **bot token** (`xoxb-`, scopes `chat:write` +
`files:write`) and a **channel id**, and **the bot must be invited to that
channel** (`/invite @yourbot`) — otherwise calls fail `not_in_channel`
(surfaced with a hint by `_friendly`). The **app token** (`xapp-`) is stored
for future Socket Mode work but isn't required to post. `media.py` makes a
looping GIF (≤24 frames, 480px, 2.5fps) and falls back to MP4 (ffmpeg) when
the GIF exceeds 3 MB. `_call` honours one HTTP-429 retry (Slack's
`Retry-After`, capped so it can't stall the locked sweep). If the file
upload fails (commonly a `missing_scope` when the app lacks `files:write`),
`_share` **degrades to a text-only post** with the summary + deep link, so
a notification still lands — the animation starts attaching automatically
once the scope is added.

Deep links the SPA honours on load (`maybeOpenDeepLink`, both reuse the
flipbook player so they ignore the active tab/filters):
- bursts → `<public_base_url>/?event=<burst_id>` (plays the sequence)
- frames → `<public_base_url>/?image=<filename>` (shows that one frame)

`public_base_url` is a single site, so for a multi-camera deployment it
points at one camera's gallery — per-camera URL mapping is a future
enhancement.

## Testing

No third-party *test-only* deps and no build step, but the suites import app
modules, so the runtime deps in [`requirements.txt`](requirements.txt)
(`requests`, `opencv-python`, `numpy`, `Pillow`) must be installed first. Run
both suites from the repo root:

```sh
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests   # backend pure helpers
bash tests/run.sh                        # SPA pure helpers (all tests/*.js suites)
```

On a headless Linux box, `import cv2` also needs the shared libraries
`opencv-python` links against (`libGL.so.1`, `libglib-2.0.so.0`); install them
with `sudo apt-get install -y libgl1 libglib2.0-0` (CI does this).

`bash tests/run.sh` is the canonical way to run the Node suites: `node
tests/*.js` would only execute the first file (the shell passes the rest as
`argv`), silently skipping the others. The runner loops over every sorted
`tests/*.js`, prints `PASS <file>` / `FAIL <file>`, stops at the first failure
with a non-zero exit, and prints a final passed/total count.

- **Python** (`tests/test_helpers.py`) imports `api_server` + the `integrations`
  package and covers the pure logic: settings validation, timezone resolution,
  the SSE detection/new-entry extractors, inference-metric rollups, Slack
  error-friendliness, media sampling, delivery recording. Inference plumbing:
  `model_chain`/`runnable_chain`/`model_is_cloud` (a low-RAM host still serves a
  cloud primary; local models filtered until RAM suffices), the rate-limit
  fallback/backoff, `concurrency_workers` clamping, and `_atomic_write_json` +
  the `_IO_LOCK`-guarded audit writes (8-thread hammer stays valid JSON, no lost
  log entries).
- **Node** suites cover the SPA's pure helpers **without** a headless browser.
  Each function the browser uses is wrapped in sentinel comments in
  `index.html` — `// === pure:NAME ===` … `// === /pure:NAME ===` — and the
  test extracts that block by regex and `eval`s it, so there's exactly one copy
  of the function (it ships in the page *and* is unit-tested). Covered today:
  `parseFilenameFields`, `relativeTime`, `dayLabel`, `resolveDisplayTz`,
  `zonedTimeToUtc`, `computeLabelStates`, `visibleLabels`, `labelsMatchFilter`,
  `smoothFlicker`, `formatDuration`, `formatSeconds`, `backfillProgress`,
  `bucketByHour`, `labelCounts`, `busiestHour`, `filterSnapshot`,
  `upsertSearch`, `removeSearch`, `mergeNewImage`, `detectionEntry`,
  `escapeHtml`, `isRecent`, `clearedFilters`, `matchesSearch`, `cardAriaLabel`, `badgeLabel`, `visitDateLabel`, `visitsSummary`, `formatCount`, `tabWrap`, `streamStatusText`.
- **Regression guard** (`tests/test_dom_refs.js`): cross-checks every
  `getElementById('x')` the SPA relies on against an `id="x"` in the markup
  (minus a tiny allowlist of runtime-created elements), catching a broken/renamed
  DOM reference the pure-helper tests can't see.
- **Inline JS sanity:** extract each `<script>` body and `node --check` it
  before deploying (catches syntax errors the single-file SPA would otherwise
  only reveal in a browser).

**Workflow (TDD):** write/extend a failing test first (confirm RED), implement
minimally (confirm GREEN), run both suites, then deploy/commit. DOM behaviour
that can't be reduced to a pure helper is proven via the deployed app.

## Linting

No build step and no runtime npm deps — both linters are opt-in and inspect the
same sources the tests use.

- **Python — [ruff](https://docs.astral.sh/ruff/)** (config: [`ruff.toml`](ruff.toml)).
  `ruff check .` selects `E,F,W,I,UP,B` and ignores a few noisy, style-only rules
  (`E501`, `E731`, `E702`, `UP031`, `UP017`). `api_server.py`,
  `analyze_images.py` and `tools/` are temporarily `extend-exclude`d while other
  changes land — drop those entries once those files are lint-clean.
  ```sh
  python3 -m pip install --user --break-system-packages ruff   # or: pipx install ruff
  ruff check .
  ```
- **JavaScript — ESLint flat config** ([`eslint.config.mjs`](eslint.config.mjs)).
  Covers `tests/*.js` (Node + browser globals, because the suites `eval`
  extracted SPA helpers) and the SPA's inline `<script>`, pulled out with
  [`tools/lint/extract-inline.mjs`](tools/lint/extract-inline.mjs). Rules are
  correctness-only (no stylistic churn) and the config imports nothing.
  ```sh
  npx --yes eslint tests/ tools/
  node tools/lint/extract-inline.mjs \
    | npx --yes eslint --stdin --stdin-filename index.inline.js
  ```
- **Editor defaults:** [`.editorconfig`](.editorconfig) pins 4-space Python,
  2-space JS/HTML, LF, a final newline, and trailing-whitespace trimming.

**CI note (npm-free):** the repo has no `package.json`, so CI never runs
`npm install`. Ruff is a single dependency-free binary; ESLint is invoked ad hoc
via `npx` when available. Dedicated lint jobs are deferred — run the commands
above locally before pushing. The CI job does install the Python runtime deps
(`pip install -r requirements.txt`) plus `libgl1`/`libglib2.0-0` before running
either suite.

## Screenshots

The README/user-guide images in `docs/guide/img/` must come from a **synthetic
fixture gallery**, never from live Webcam21/Webcam22 footage. (`docs/img/`
is gitignored — old live-camera captures; do not commit it.)

- Point `SCREENSHOT_ROOT` at the fixture tree (the default). **`SCREENSHOT_ROOT`
  must not be `/mnt/models/Webcam21` or `/mnt/models/Webcam22`.** The proxy
  exits 2 if it is.
- `/api` is **stubbed** from `tools/screenshots/fixtures/api/` by default
  (`SCREENSHOT_API=stub`). Do not use `SCREENSHOT_API=live` for published
  shots. There is no `SCREENSHOT_PLACEHOLDER` flag.
- Playwright lives in a scratch dir, not the repo. Tooling:
  [`tools/screenshots/`](tools/screenshots/README.md). `shots.js` drives
  Chromium. The script freezes animations, kills JS timers, and pre-loads
  lazy images so `page.screenshot()` doesn't hang. It waits for
  `domcontentloaded`, never `networkidle` (the SSE stub stays open).
  `PUBLISH_GUIDE_IMG=1 bash tools/screenshots/run_shots.sh` copies the
  named map into `docs/guide/img/` — do not glob-copy `desktop-*.png`.

Playwright-as-CI is still deferred ([ROADMAP.md](ROADMAP.md)); this harness is
only for fixture-first stills.

## Services & infrastructure

Box-specific: paths, units, hosting, Ollama/YOLO install locations, and
operational history all live in [DEPLOYMENT.md](DEPLOYMENT.md). The app
expects only: image dirs (`watch_dirs`), a static web server per dir,
something invoking `create-index.sh <dir>` per camera (inotify
recommended, polling fallback), and an Ollama endpoint (`ollama_url`).

## Deployment

```bash
# UI changes take effect immediately:
cp index.html lucide.min.js /mnt/models/Webcam21/
cp index.html lucide.min.js /mnt/models/Webcam22/
# Help walkthrough (create-index.sh also copies these every sweep):
cp docs/USER-GUIDE.html /mnt/models/Webcam21/ /mnt/models/Webcam22/
mkdir -p /mnt/models/Webcam21/guide/img /mnt/models/Webcam22/guide/img
cp docs/guide/img/*.png /mnt/models/Webcam21/guide/img/
cp docs/guide/img/*.png /mnt/models/Webcam22/guide/img/

# Pipeline changes: nothing to do - each sweep runs the script fresh.
# API changes: sudo systemctl restart webcam-api
# Unit changes: sudo bash systemd/install.sh
```

**Public access / write API.** A host reverse proxy (nginx, Certbot TLS)
fronts each camera on its own subdomain with basic-auth (`.htpasswd`):
`webcam.…→localhost:8180`. The host `dogcam.…` vhost still targets legacy `localhost:8280` (nothing listening); live dogcam/Webcam22 gallery is `:8180/Webcam22/` until that host proxy is updated. To make the write
API work over the public URL **without exposing a second port**, each
vhost also proxies `/api/` to the API, behind the same basic-auth:

```nginx
location /api/ {
    proxy_pass http://localhost:8190;          # NB: no trailing slash — keep the /api/ prefix
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    auth_basic "Restricted Content";
    auth_basic_user_file /etc/nginx/.htpasswd;
}
```

This is one of two auth layers, not the only one. The API has its own gate:
`WEBCAM_API_TOKEN` (or the `api_token` key in `settings.json`) makes every
`POST` require `Authorization: Bearer <token>` or HTTP Basic, and
`WEBCAM_API_HOST` binds it to `127.0.0.1` by default. So `:8190` is only
unauthenticated *when no token is configured*; treat the token as set and
keep the port off the public internet regardless.

## Troubleshooting

- **Gallery empty in Objects tab** — usually hidden labels (localStorage
  blacklist) or no verified detections for the filter; the empty state
  offers "Unhide all labels" / "Show all images".
- **Images vanishing** — grep journals for `Retention: removed` and
  `Pruned`; check `retention_log.json` and the UI “Last cleanup” line. If
  those are absent, check root’s crontab and `/etc/cron.d/` for a foreign
  `find … rm` (legacy problem). Watchdog log: `watchdog.log`.
- **Disk full / retention not running** — confirm
  `python3 -c 'import json; json.load(open("analysis.json"))'` succeeds
  (corrupt file used to abort every sweep). Force cleanup:
  `tools/watchdog.sh retention` or
  `python3 analyze_images.py --retention-only`. Check per-camera
  `budget_pct` via `curl -s localhost:8190/api/status | jq .cameras`.
  Remember host free space can be exhausted by Ollama/projects even when
  each camera dir is under `max_dir_gb`.
- **`/api/health` is 503 / `recent_sweep: false`** — no successful
  `lastrun` within the health window (1 h). Inspect
  `journalctl -u webcam-pipeline@Webcam21` for crashes; run
  `tools/watchdog.sh check` (restarts stuck units if sweep age ≥ 2 h).
- **Watchdog restarts looping** — should be limited by
  `/tmp/webcam_watchdog_last_restart`. If units flap, inspect analyze
  errors (missing YOLO weights, disk full, import errors).
- **No deep passes happening** — `curl localhost:11434/api/version`
  (Ollama up?), check journal `System Check` line for runnable chain /
  `Deep passes: ON|OFF`, and free RAM vs `min_mem_for_local_gb`. Also
  confirm `deep_passes_enabled` in settings.
- **Pin/delete/settings/integrations failing in UI** — `webcam-api` down;
  or, over the public URL, the vhost is missing the `/api/` proxy block
  (symptom: the panel hangs at "checking…" because `/api/…` 404s or the
  old `:8190` origin isn't reachable). On the LAN, check the phone can
  reach `:8190` directly.
- **False bursts spanning days** — the sweep sorts frames by mtime before
  burst detection, precisely so consecutive entries always have a positive gap.
  If you see one anyway, the usual cause is a camera whose clock jumped
  backwards mid-burst; check the filenames in the reported burst and see step 3.
- Logs:
  - Pipeline: `journalctl -u webcam-pipeline@Webcam21 -f` (and `@Webcam22`,
    `webcam-api`, `ollama`)
  - Watchdog: `tail -f /home/user/webcam/watchdog.log` /
    `journalctl -t webcam-watchdog -f`
