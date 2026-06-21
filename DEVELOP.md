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
- Runs `analyze_images.py`, then syncs `index.html`, the PWA assets
  (`manifest.json`, `icon.svg`), and JSON artifacts into the camera web root.
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
   COCO classes mapped to person/car/bird/cat/dog, conf 0.45, ~0.2s/image.
   Haar = legacy frontal-face/fullbody/frontalcatface cascades.
5. **Deep pass** — the `model_primary` vision model through Ollama
   (`analyze_image_local`, /api/chat with `format: json`). `model_primary` can be
   a local tag *or* an Ollama `:cloud` model (e.g. `minimax-m3:cloud`) — same
   `:11434` path either way. OpenRouter remains a separate fallback when
   `allow_cloud` is true. Budgeted: `max_deep_passes` per camera per sweep
   counts local AND cloud calls. **An LLM verdict replaces the entry
   wholesale — the LLM always trumps the fast-pass detector.**
   - *Which models run here* (`runnable_chain`): the primary→fallback chain is
     filtered to what THIS host can serve — a `:cloud` model always (it runs on
     Ollama's servers, no local RAM), a local model only when free RAM ≥
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
     is recorded as a partial but does NOT consume urgent budget.
6. **Idle backfill** (`deep_backfill`) — leftover budget verifies
   fast-pass negatives and ignored-label partials, closest-to-a-detection
   first then newest, so the archive converges on LLM verdicts. Fanned out
   across `deep_concurrency` workers (`concurrency_workers` clamps to
   `[1, len(targets)]`); results are consumed in the main thread so
   `analysis_data` is never mutated concurrently, and a peer tripping
   `RATE_LIMITED` short-circuits the rest. The audit-trail files
   (`inference_status.json`/`inference_log.json`) are written via
   `_atomic_write_json` under `_IO_LOCK` so concurrent passes and the live
   API reader never see a half-written file.
7. **Bursts** — consecutive images < `burst_threshold_seconds` apart form
   a burst; bursts containing a detection get an LLM sequence summary
   (last 3 frames). Invalid cached bursts (missing files, or spans
   violating the chain rule) are pruned and re-detected. A new summary is
   fanned out to enabled notifiers — see [Integrations](#integrations).
8. **Pruning** — analysis/burst entries whose files no longer exist
   (retention, API delete, external cleanup) are removed.

Inference timing on this box (4-core, no GPU): **local** vision models don't
fit RAM here, so the deep pass runs on the cloud primary (`minimax-m3:cloud`)
at **~7–25 s/image** (rising under `deep_concurrency`, which the endpoint only
partially parallelizes — ~1.4× at 3). Sustained net catch-up is therefore
~300–360 frames/hr; the historical backfill of a large archive takes many
hours and is meant to grind across sweeps. `max_deep_passes` bounds a sweep so
it can't hold the 1h lock indefinitely.

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
Stdlib-only HTTP server, the single write channel (nginx mounts are ro). It
exposes 9 routes — `pins`, `pin`, `delete`, `settings` (GET/POST), `integrations`
(GET/POST + `/test`), `status`, `health`, `inference_log`, and the `events` SSE
stream (`image.new` / `detection.preliminary` / `new-detection` / `new-burst`;
`X-Accel-Buffering: no` so it survives the proxy unbuffered).

**Methods, request/response payloads, status codes, and the SSE event schema
are documented once in [API.md](API.md)** — the source of truth; keep it in
sync with the code. Two things worth repeating here: the only settings
`POST /api/settings` will accept are `fast_pass_engine`, `deep_backfill`,
`deep_passes_enabled`, `idle_sweep_seconds`; and `GET /api/integrations` is
always **redacted** (token presence, never values — see [Integrations](#integrations)).

API origin: the UI calls the API **same-origin at `/api/`** so there's no
second port to expose publicly — the reverse proxy maps `/api/` to
`localhost:8190` behind its basic-auth (see Deployment), falling back to
`http://<host>:8190` only when a page is opened **directly** on a camera
container (`:8180`/`:8280`, i.e. LAN/dev). Restart after editing:
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
| `integrations.json` | api_server | Slack secrets; gitignored, mode 600 (see [Integrations](#integrations)) |

`analysis.json` entry states:
- `{"fast_pass": "negative"}` — detector saw nothing; LLM hasn't looked.
- `{labels..., "fast_pass": "partial"}` — detector hit, awaiting LLM
  (UI: "Unverified" / `label?` badges).
- `{labels...}` (no `fast_pass` key) — LLM verdict, final.
  All-false = verified clear.

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

| Key | Default | Meaning |
|-----|---------|---------|
| `burst_threshold_seconds` | 300 | max gap between burst frames |
| `idle_sweep_seconds` | 60 | idle re-scan cadence (UI-settable, 15–3600) |
| `timezone` | Australia/Sydney | display TZ; `WEBCAM_TZ` env overrides (see [Paths & XDG](#paths--xdg)) |
| `max_age_days` | 30 | retention: age limit for no-detection images |
| `max_dir_gb` | 3.0 | retention: per-camera disk budget (code fallback 4.0) |
| `min_mem_for_local_gb` | 6.0 | min free RAM to attempt a **local** model; a `:cloud` model ignores this (see `runnable_chain`) |
| `allow_cloud` | false | permit OpenRouter fallback (separate from an Ollama `:cloud` primary) |
| `ollama_url` | http://localhost:11434 | local LLM endpoint |
| `model_local` | gemma4:12b | Ollama model tag (back-compat default for `model_primary`) |
| `model_primary` | (=`model_local`) | primary inference model via Ollama (local tag or a `:cloud` model) |
| `model_fallback` | "" | optional fallback tried when the primary errors/rate-limits |
| `max_deep_passes` | 4 | LLM calls per camera per sweep (local+cloud) |
| `deep_concurrency` | 1 | parallel **backfill** deep passes. Safe only with a `:cloud` model (no local RAM contention); the cloud endpoint partially parallelizes (~1.4× at 3). Rate-limit backoff + per-sweep `RATE_LIMITED` still guard it |
| `fast_pass_engine` | yolo | `yolo` or `haar` (UI-selectable) |
| `deep_passes_enabled` | true | master switch for ALL Gemma work (priority+backfill+bursts); false = detector-only, no LLM (UI-toggleable) |
| `deep_backfill` | true | idle LLM verification of the archive (UI-toggleable) |
| `gate_ignore_labels` | ["car"] | labels that alone don't trigger urgent deep passes |
| `camera_offline_hours` | 24 | no frames in this long → a Slack "camera offline?" alert |

## Observability & health alerts

Beyond `/api/status` + `/api/health` (above), the pipeline pushes **debounced
Slack alerts** at the end of each sweep (`run_health_checks` in
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
view. Schema:

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
    "server_url": "https://ntfy.sh",   "topic": "my-secret-topic",
    "token": "tk_… (optional, for protected topics)",
    "public_base_url": "https://dogcam.example.org",
    "notify_mode": "context"
  }
}
```

ntfy is config-file-driven (no UI panel yet): it pushes the summary/caption as
the message body with a **Click** link back into the gallery (needs
`public_base_url`); unlike Slack it does **not** upload the frame. Enable by
adding the block above and setting `enabled: true`.

`notify_mode` (per integration) controls *what* triggers a post:
- `context` (default, quietest) — only burst/sequence summaries
  (`notify_burst`), the contextual narrative + frame animation.
- `objects` — every freshly-analyzed frame **with a detection**
  (`notify_image`).
- `all` — every freshly-analyzed frame, detections and clears alike.

Per-image modes fire only on **priority** deep passes (newly-arrived
detector hits), never the idle backfill of the archive — that would flood
the channel. Volume is therefore bounded by `max_deep_passes` per sweep.

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

No third-party test deps and no build step — the same constraints as the app.
Run both suites from the repo root:

```sh
python3 -m unittest discover -s tests   # backend pure helpers
node tests/*.js                          # SPA pure helpers (one file each)
```

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

## Screenshots

The README/user-guide images in `docs/img/` are captured from the **live app**
with Playwright (Chromium) — kept out of the test suite and dependency tree
(installed into a scratch dir, not the repo). Tooling + run instructions:
[`tools/screenshots/`](tools/screenshots/README.md). In brief: `proxy.py`
serves the deployed gallery and reverse-proxies `/api`→:8190 under one local
origin (bypassing nginx basic-auth and CORS), then `shots.js` drives Chromium
across views/viewports. The script freezes animations, kills JS timers, and
pre-loads lazy images so `page.screenshot()` doesn't hang on the SPA's
continuous repaint. (This supersedes the earlier "Playwright deferred" note.)

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

**Public access / write API.** A host reverse proxy (nginx, Certbot TLS)
fronts each camera on its own subdomain with basic-auth (`.htpasswd`):
`webcam.…→localhost:8180`, `dogcam.…→localhost:8280`. To make the write
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

This is what gives the API its auth — `:8190` itself is unauthenticated,
so it must NOT be port-forwarded publicly; only the proxied `/api/` path
should be reachable from outside the LAN.

## Troubleshooting

- **Gallery empty in Objects tab** — usually hidden labels (localStorage
  blacklist) or no verified detections for the filter; the empty state
  offers "Unhide all labels" / "Show all images".
- **Images vanishing** — grep journals for `Retention: removed` and
  `Pruned`; if absent, check root's crontab and `/etc/cron.d/`.
- **No deep passes happening** — `curl localhost:11434/api/version`
  (Ollama up?), check journal `System Check` line for
  `Local LLM Enabled: True`, and free RAM vs `min_mem_for_local_gb`.
- **Pin/delete/settings/integrations failing in UI** — `webcam-api` down;
  or, over the public URL, the vhost is missing the `/api/` proxy block
  (symptom: the panel hangs at "checking…" because `/api/…` 404s or the
  old `:8190` origin isn't reachable). On the LAN, check the phone can
  reach `:8190` directly.
- **False bursts spanning days** — the chronological sort in
  analyze_images.py was removed at some point; see step 3 above.
- Logs: `journalctl -u webcam-pipeline@Webcam21 -f` (and `@Webcam22`,
  `webcam-api`, `ollama`).
