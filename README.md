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
- **Retention** — per-camera byte budget (3 GB each) + 30-day age limit; pin to
  protect forever, delete to remove; detections are pruned only under disk
  pressure.
- **Observability** — `/api/status`, `/api/health` (4 checks), a live inference
  audit trail, camera-liveness and disk stats.
- **Live updates (partial)** — an SSE `/api/events` stream pushes
  `new-detection` / `detection.preliminary` / `new-burst` to the open gallery
  (via a 3 s file-mtime bridge), with graceful fallback to polling. Preliminary
  (detector-only) hits surface live too — important when deep passes are off.
- **Integrations** — Slack (image/animation + link-back, notify-mode
  context/objects/all) on a pluggable dispatcher; secrets isolated and never
  web-synced.
- **Configurable display timezone** — `WEBCAM_TZ` env > `settings.json` >
  `Australia/Sydney`; filename timestamps parsed **DST-aware**.
- **Test harness** — 13 Python `unittest` + 7 Node pure-helper suites.

### Remaining / in progress

- **Live streaming, the headline pieces** — `image.new` events, live **LLM
  token streaming** ("AI is looking at this…" with the caption typing in), and
  a true pipeline→API push (today's bridge is a 3 s mtime poll; the Ollama call
  is non-streaming). `detection.preliminary` now ships; see [ROADMAP.md](ROADMAP.md).
- **API spec** — no OpenAPI / event-schema doc yet.
- **Browser/integration tests** — Playwright not yet incorporated (pure-logic
  helpers are covered; DOM behavior is proven via the deployed app).
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

## Using the gallery

- **Timeline tab** (default) — one card per object "visit" ("Person visit
  · 7:02–7:08 · 6 min", with an AI caption of what happened). Tap a visit
  to play it as a short animation; tap the image to step frame-by-frame.
- **Objects tab** — only images with AI-verified detections.
- **All tab** — every snapshot.
- **Object buttons** (person, dog, cat, car…) appear automatically for
  whatever the AI has detected. Tap to filter, tap again to clear. Face and
  body detections are merged into **person** by default.
- **Day planner chart** — each bar is a day (top = midnight); red marks show
  *when* the filtered object was seen. Tap a day to drill into its hourly
  histogram below.
- **Detection lifecycle badges** — solid badge = verified (both AIs agree),
  amber `label?` = preliminary (fast detector only, awaiting the LLM),
  struck-through red = disputed (the two AIs disagree; hidden in precision
  mode unless "Show unconfirmed detections" is on).
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
- Old images with no detections are deleted after 30 days, or sooner if a
  camera exceeds its 3 GB disk budget. Pinned images and images with
  detections are kept (detections are only pruned under disk pressure).

## Documentation map

- **[ROADMAP.md](ROADMAP.md)** — what remains, in detail (the live-streaming milestone).
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — how it fits together today, and how earlier versions looked.
- **[DEVELOP.md](DEVELOP.md)** — architecture, configuration, and operations reference.
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — host/service/proxy setup.
