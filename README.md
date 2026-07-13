# Webcam AI Gallery

A self-hosted, AI-assisted gallery for two home security cameras — fully local,
nothing leaves the box.

## Vision

Security cameras produce thousands of near-identical motion frames a day. The
useful information — *who or what was here, when, and for how long* — is buried
in that pile, and finding it means scrubbing endlessly.

This project turns that raw stream into a **queryable timeline of events** you
can answer in plain language: *"When was a person at the gate?"*, *"When did
the car leave?"*, *"Was the dog out back this afternoon?"* A fast detector flags
motion subjects within seconds; a local vision LLM then reasons about each scene
and writes a short caption, so the gallery shows you **visits** ("Person visit ·
7:02–7:08 · 6 min") instead of a wall of thumbnails.

Three principles guide it:

- **Local-first and private.** Every frame, every inference, every byte stays on
  this machine. Cloud inference exists only behind an explicit, default-off kill
  switch. No image ever leaves the box.
- **Live, as it happens.** The pipeline knows the instant something occurs; the
  goal is for the gallery to surface it in near real-time — new frames,
  preliminary detections, and the LLM's reasoning streaming in — not on the next
  poll tick. *(Partially built — see status below.)*
- **Honest and extensible.** It's a focused two-camera homelab project, not an
  enterprise VMS. New integrations and features are welcome **via pull request**
  (see [DEVELOP.md](DEVELOP.md)).

## Project status

An honest snapshot — verified against the code, not aspirational.

### Built and working

- **Two-camera local gallery**, fully self-hosted; cloud inference off by
  default (`allow_cloud=false` kill switch).
- **Event-driven ingest** — an `inotifywait` watcher queues each new snapshot
  for analysis within seconds (no fixed-interval scanning).
- **Tiered AI vision** — a fast YOLO pass (person/car/bird/cat/dog) followed by
  a local Gemma vision LLM (`gemma4:12b` via Ollama) deep pass, combined with a
  **detector↔LLM consensus** rule to cut false positives.
- **Detection lifecycle** — `preliminary` (fast detector only) → `verified`
  (both agree) → `disputed` (they disagree). Outside precision mode, all
  inference labels are shown.
- **Timeline of visits** — contiguous presence runs grouped into one card each,
  with a Gemma caption and a flipbook animation; plus Objects/All tabs, auto
  object filters, label aliasing, blacklist, and day/hour activity charts.
- **Retention (file rotation)** — per-camera byte budget (3 GB each) + 30-day
  age limit for empty frames; pin to protect forever, delete to remove;
  detections are pruned only under disk pressure; unanalyzed backlog can be
  evicted only if still over budget. Catalogs recover from truncated writes.
- **Cron watchdog** — independent of systemd: every 15 min checks services /
  stuck sweeps; hourly runs the same pin-aware retention from `settings.json`
  (not a blind `find | rm`).
- **Observability** — `/api/status`, `/api/health` (4 checks), a live inference
  audit trail, camera-liveness, disk stats, last retention event.
- **Live updates (partial)** — an SSE `/api/events` stream pushes
  `image.new` / `new-detection` / `detection.preliminary` / `new-burst` to the
  open gallery (via a 3 s file-mtime bridge), with graceful fallback to polling.
  New frames and detector-only hits surface live too — important when deep
  passes are off.
- **Integrations** — Slack (image/animation + link-back) and ntfy (push +
  click-through link) on a pluggable dispatcher (notify-mode
  context/objects/all); secrets isolated and never web-synced.
- **Configurable display timezone** — `WEBCAM_TZ` env > `settings.json` >
  `Australia/Sydney`; filename timestamps parsed **DST-aware**.
- **Installable PWA** — a web app manifest makes the gallery "Add to Home
  Screen"-able (standalone, themed); no service worker, so no stale-cache risk.
- **Insights** — the ℹ status panel shows content stats over all analysed
  frames: total, busiest hour, a 24-hour activity sparkline, and most-seen
  objects.
- **Saved searches** — name the current filters (tab + objects + search +
  date + time range) and recall them as one-tap chips in the sidebar
  (localStorage, per-device).
- **Clip export** — download any visit as an animated GIF (or MP4) from the
  flipbook player; the server builds it on demand from the frames.
- **Test harness** — stdlib Python `unittest` for backend helpers + Node
  assert suites for the SPA's pure helpers, no third-party deps
  (see [DEVELOP.md](DEVELOP.md#testing)).

### Remaining / in progress

- **Live streaming, the headline piece** — live **LLM token streaming** ("AI is
  looking at this…" with the caption typing in), plus a true pipeline→API push
  and incremental DOM updates (today's bridge is a 3 s mtime poll that triggers
  a full refetch; the Ollama call is non-streaming). `image.new` /
  `detection.preliminary` now ship; see [ROADMAP.md](ROADMAP.md).
- **API spec** — endpoints + the SSE event schema are documented in
  [API.md](API.md); a formal OpenAPI spec is still optional/deferred.
- **Browser/integration tests** — Playwright **deferred** (the SPA's logic is
  covered via extracted pure helpers; DOM behavior is proven via the deployed
  app). Rationale in [ROADMAP.md](ROADMAP.md).
- **`WEBCAM_TZ` in the systemd unit** — not yet pinned (needs sudo); the
  `settings.json` default covers it meanwhile.
- **Deferred by design** — stream authentication, multi-client backpressure,
  historical event replay.

### Current-instance caveats (this box, today)

- **Gemma deep passes are disabled** to save resources — the gallery runs in
  detector-only mode, so verdicts stay `preliminary` and a backlog of images
  awaits the LLM backfill, which catches up when deep passes are re-enabled.
- **Webcam22 has been offline** (no new frames) — operational, not a code issue.

## Viewing

| Camera | URL |
|--------|-----|
| Webcam (front, car in frame) | `http://<host>:8180` |
| Dogcam | `http://<host>:8280` |

## Screenshots

_Captured from the live app via Playwright (`tools/screenshots/`, see
[DEVELOP.md](DEVELOP.md#screenshots))._

**Objects tab** — the gallery with the date timeline, motion-activity chart,
object filter chips, and cards carrying colour-coded detection badges:

![Objects tab with detection badges and sidebar](docs/img/gallery-objects.png)

**Gallery grid** — the core browsing view (medium density):

![Gallery grid view](docs/img/gallery-grid.png)

**Lightbox** — full-screen review with metadata, frame stepping, and controls:

![Full-screen lightbox viewer](docs/img/lightbox.png)

## Using the gallery

- **Timeline tab** (default) — one card per object "visit" ("Person visit
  · 7:02–7:08 · 6 min · 4 frames"; brief visits read in seconds, e.g.
  "· 25 sec", with an AI caption of what happened). Tap a visit to play it as
  a short animation; tap the image to step frame-by-frame.
- **Objects tab** — only images with AI-verified detections.
- **All tab** — every snapshot.
- **Filters popover** — object filters (person, dog, cat, car…) live behind a
  **Filters** button with a live count badge and an inline summary of what's
  active; they're generated automatically from whatever the AI has detected.
  Pick one or several to filter; clear them from the same popover. Face and
  body detections are merged into **person** by default.
- **Time range** — the toolbar's time-of-day filter collapses to a single line
  at the all-day default and expands when you click it (or narrow the range).
- **Day planner chart** — each bar is a day (top = midnight); red marks show
  *when* the filtered object was seen. Tap a day to drill into its hourly
  histogram below.
- **Detection badges** — a calm tinted tag per detected object. With the LLM
  deep-pass enabled a badge can be preliminary (fast detector only, awaiting the
  LLM) or disputed (the two AIs disagree — struck-through red, hidden in
  precision mode unless "Show unconfirmed detections" is on). When deep passes
  are off (detector-only, this box today) the detector is the final verdict, so
  tags show plainly — no "awaiting" amber state.
- **Pin** (📌) an image to protect it from automatic cleanup forever.
  **Delete** (🗑) removes an image from the server permanently.
- **Settings menu** (gear icon) — hide noisy labels, label merging,
  show/hide disputed detections, fast-detector choice, idle deep analysis
  on/off, and sweep/poll intervals.
- **AI button** — pulsing amber with elapsed time while the LLM is
  analyzing; tap for pipeline status and the inference audit trail.

## What happens automatically

- New snapshots are detected within seconds and queued for analysis.
- People/dogs/cats jump the queue; everything else is verified when idle,
  newest first.
- **Retention** (each analysis sweep, and again on an hourly cron):
  - analyzed frames with **no** detections older than **30 days** are removed;
  - if a camera dir exceeds its **3 GB** image budget, oldest empty frames go
    first, then oldest detections if still over budget;
  - if still over budget (e.g. after a pipeline outage left many unanalyzed
    files), oldest unanalyzed frames can be removed next;
  - **pinned** images are never auto-deleted; thumbs go with their parents.
- A **cron watchdog** restarts dead or stuck pipeline services and forces that
  same retention path hourly so cleanup does not depend only on a long-lived
  analyze process staying healthy. Details:
  [DEVELOP.md — Retention](DEVELOP.md#retention-file-rotation) and
  [DEVELOP.md — Watchdog](DEVELOP.md#cron-watchdog-toolswatchdogsh).

## Documentation map

- **[ROADMAP.md](ROADMAP.md)** — what remains, in detail (the live-streaming milestone).
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — how it fits together today, and how earlier versions looked.
- **[API.md](API.md)** — REST endpoints + the SSE event schema (request/response payloads).
- **[DEVELOP.md](DEVELOP.md)** — architecture, configuration, retention, watchdog, and operations reference.
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — host/service/proxy setup and cron install on this box.
