# Webcam AI Gallery

A self-hosted, AI-assisted gallery for two home security cameras — local-first.
Frames stay on this machine. Slack notifications and optional Home Assistant
MQTT **may leave the box**.

How to use it: **[docs/USER-GUIDE.md](docs/USER-GUIDE.md)**.

## Vision

Security cameras produce thousands of near-identical motion frames a day. The
useful information — *who or what was here, when, and for how long* — is buried
in that pile, and finding it means scrubbing endlessly.

This project turns that raw stream into a **queryable timeline of events**:
*"When was a person at the gate?"*, *"When did the car leave?"*, *"Was the dog
out back this afternoon?"* A fast detector flags motion subjects within
seconds. A local vision LLM then answers a **fixed Home Assistant schema**
(flags such as `postal_delivery`, `porch_access`, `animal_detected`) — it does
**not** write free-text captions, and it **never overwrites** detector labels
(`person` / `car` / `dog` / …). The gallery groups those hits into **visits**
("Person visit · 7:02–7:08 · 6 min") instead of a wall of thumbnails.

Three principles guide it:

- **Local-first.** Every frame and every inference stays on this machine unless
  you opt into an outbound integration. Cloud inference exists only behind an
  explicit, default-off kill switch (`allow_cloud=false`). Slack (if enabled)
  posts off-box. Optional HA MQTT publishes retained flags to a broker.
- **Live, as it happens.** The pipeline knows the instant something occurs; the
  goal is for the gallery to surface it in near real-time — new frames,
  preliminary detections, and scene flags streaming in — not on the next
  poll tick. *(Partially built — see status below.)*
- **Honest and extensible.** It's a focused two-camera homelab project, not an
  enterprise VMS. New integrations and features are welcome **via pull request**
  (see [DEVELOP.md](DEVELOP.md)).

## Project status

An honest snapshot — verified against the code and live `settings.json`, not
aspirational.

### Built and working

- **Two-camera local gallery**, fully self-hosted; cloud inference off by
  default (`allow_cloud=false` kill switch).
- **Event-driven ingest** — an `inotifywait` watcher queues each new snapshot
  for analysis within seconds (no fixed-interval scanning).
- **Tiered AI vision** — a fast YOLO pass (person/car/bird/cat/dog) followed by
  a local vision model (`gemma4:e2b` via Ollama). The deep pass writes
  **Home Assistant flags** (`postal_delivery`, `porch_access`, `animal_detected`,
  `dog_walked`, `clothes_drying`, `weapon_detected`, …). It does **not** emit
  free-text captions. Detector labels are **never overwritten** by the
  LLM (`merge_llm_into_fastpass`).
- **Detection lifecycle** — `preliminary` (fast detector only, awaiting flags)
  then a merged record: YOLO objects stay; HA flags attach under `_llm` and as
  sibling keys. The older detector↔LLM “consensus / disputed” UI still exists
  for YOLO-class names, but e2b no longer claims `person`/`dog`, so that path
  is mostly leftover.
- **Timeline of visits** — contiguous detector presence runs grouped into one
  card each, with a flipbook animation; plus Objects/All tabs, auto object
  filters, label aliasing, blacklist, and day/hour activity charts. Scene flags
  show as HA badges. Timeline cards read `analysis.description` for a caption
  — the current model **does not write that field**.
- **Retention (file rotation)** — per-camera byte budget (**5.0 GB** each on
  this box) + 30-day age limit for empty frames; pin to protect forever, delete
  to remove; detections are pruned only under disk pressure; unanalyzed backlog
  can be evicted only if still over budget. Catalogs recover from truncated
  writes.
- **Cron watchdog** — independent of systemd: every 15 min checks services /
  stuck sweeps; hourly runs the same pin-aware retention from `settings.json`
  (not a blind `find | rm`).
- **Observability** — `/api/status`, `/api/health` (4 checks), a live inference
  audit trail, camera-liveness, disk stats, last retention event, plus
  `GET /api/llm-schema` for the live prompt/HA schemas.
- **Live updates (partial)** — an SSE `/api/events` stream pushes
  `image.new` / `new-detection` / `detection.preliminary` / `new-burst` to the
  open gallery (via a 3 s file-mtime bridge), with graceful fallback to polling.
  New frames and detector-only hits surface live too.
- **Integrations** — Slack (image/animation + link-back; **leaves the box**)
  and optional Home Assistant MQTT (retained flags; **may leave the box**).
  ntfy exists as a config-file provider (no settings UI). Secrets isolated and
  never web-synced. Slack `notify_mode: context` only fires on burst summaries
  — with summaries off, that path is silent.
- **Configurable display timezone** — `WEBCAM_TZ` env > `settings.json` >
  `Australia/Sydney`; filename timestamps parsed **DST-aware**.
- **Installable PWA** — a web app manifest makes the gallery "Add to Home
  Screen"-able (standalone, themed); no service worker, so no stale-cache risk.
- **Insights** — the ℹ status panel shows content stats over analysed
  frames: total, busiest hour, a 24-hour activity sparkline, most-seen
  objects, and the HA schema viewer.
- **Saved searches** — name the current filters (tab + objects + search +
  date + time range) and recall them as one-tap chips in the sidebar
  (localStorage, per-device).
- **Clip export** — download a visit as an **animated GIF** from the flipbook
  player (`POST /api/clip`). The UI is GIF only; MP4 is API-only.
- **Test harness** — stdlib Python `unittest` for backend helpers + Node
  assert suites for the SPA's pure helpers, no third-party deps
  (see [DEVELOP.md](DEVELOP.md#testing)).

### Remaining / in progress

- **Live streaming, the headline piece** — per-image **LLM token streaming**
  ("AI is looking at this…") and a true pipeline→API push. Today's SSE bridge
  is a 3 s mtime poll; the client patches images/detections in memory, but
  `new-burst` still refetches. Burst analysis can stream into the ℹ panel;
  the per-image Ollama call is still `stream: False`. See [ROADMAP.md](ROADMAP.md).
- **API spec** — endpoints + the SSE event schema are documented in
  [API.md](API.md); a formal OpenAPI spec is still optional/deferred.
- **Browser/integration tests** — Playwright **deferred** as CI (the SPA's
  logic is covered via extracted pure helpers). Screenshot tooling is separate
  (see [DEVELOP.md](DEVELOP.md#screenshots)).
- **`WEBCAM_TZ` in the systemd unit** — not yet pinned (needs sudo); the
  `settings.json` default covers it meanwhile.
- **Deferred by design** — stream authentication, multi-client backpressure,
  historical event replay.

### Current-instance knobs (this box, today)

Verified against live `settings.json` and `GET /api/status` (no camera JPEGs
opened). These are **not** the import-time fallbacks in `analyze_images.py`.

| Knob | On this box |
|------|-------------|
| `model_primary` / `model_local` / `model_fallback` | `gemma4:e2b` (~40 s/image) |
| `deep_passes_enabled` | **true** |
| `deep_backfill` | **false** (archive is not fully LLM-flagged) |
| `burst_summaries_enabled` | **false** |
| `allow_cloud` | false |
| `max_dir_gb` | **5.0** |
| `max_deep_passes` | 30 |

Slack is enabled with `notify_mode: context`; with burst summaries off, those
notifications do not fire. HA MQTT is enabled and publishes off-box. Camera
liveness is on `/api/health` (`cameras[].stale`) — do not take an offline
claim from this README.

## Viewing

How to use the gallery: **[docs/USER-GUIDE.md](docs/USER-GUIDE.md)**.

| Camera | URL |
|--------|-----|
| Webcam (front, car in frame) | `http://<host>:8180` |
| Dogcam | `http://<host>:8280` |

Use **Switch feed** in the page to jump between the two.

## Screenshots

These images are from a **synthetic fixture gallery**, never real camera
footage. Do not point the screenshot harness at `/mnt/models/Webcam21` or
`Webcam22`. Published stills live in [`docs/guide/img/`](docs/guide/img/)
(see [DEVELOP.md](DEVELOP.md#screenshots) and
[tools/screenshots/README.md](tools/screenshots/README.md)).

**Timeline** (default) — one card per visit, not a pile of near-identical JPEGs:

![Timeline with a dog visit and a person visit](docs/guide/img/timeline.png)

**Objects tab** — frames the detector marked, with colour badges and scene flags:

![Objects tab with detection badges and sidebar](docs/guide/img/objects.png)

**All snapshots** — every motion still, including empty / CLEAR frames:

![All-snapshots grid including empty frames](docs/guide/img/all-grid.png)

**Lightbox** — full-screen review with date/time in the heading (no LAN IP):

![Full-screen lightbox viewer](docs/guide/img/lightbox.png)

## Using the gallery

- **Timeline tab** (default) — one card per object "visit" ("Person visit
  · 7:02–7:08 · 6 min · 4 frames"; brief visits read in seconds, e.g.
  "· 25 sec"). Tap a visit to play it as a short animation; tap the image to
  step frame-by-frame. Download GIF from the player. Free-text AI captions
  are **not** produced by the current e2b pass.
- **Objects tab** — frames the fast detector marked (person/car/dog/…). HA
  scene flags can also appear as badges.
- **All tab** — every snapshot.
- **Filters popover** — object filters (person, dog, cat, car…) live behind a
  **Filters** button with a live count badge and an inline summary of what's
  active. Pick one or several to filter; clear them from the same popover.
  Face and body detections are merged into **person** by default.
- **Saved searches** — name the current filters and recall them as chips
  (this browser only).
- **Time range** — the toolbar's time-of-day filter collapses to a single line
  at the all-day default and expands when you click it (or narrow the range).
- **Day planner chart** — each bar is a day (top = midnight); red marks show
  *when* the filtered object was seen. Tap a day to drill into its hourly
  histogram below.
- **Detection badges** — a tinted tag per detector object, plus HA scene flags
  (`postal_delivery`, `porch_access`, `animal_detected`, …) when a deep pass
  has run. A badge can still show preliminary (awaiting the LLM) if the
  detector hit has no flags yet. The older “disputed” (struck-through) state
  is leftover consensus UI; e2b does not overwrite YOLO labels.
- **Pin** (📌) an image to protect it from automatic cleanup forever.
  **Delete** (🗑) removes an image from the server permanently.
- **Settings menu** (gear icon) — hide noisy labels, label merging,
  show/hide disputed detections, fast-detector choice, idle deep analysis
  (backfill) on/off, and sweep/poll intervals.
- **AI button** — pulsing amber with elapsed time while the LLM is
  analyzing; tap for pipeline status, HA flags/schema, and the inference
  audit trail.

## What happens automatically

- New snapshots are detected within seconds and queued for analysis.
- People/dogs/cats jump the queue for an e2b flag pass; parked-car-only
  frames do not consume urgent budget. Idle archive backfill is a separate
  switch (`deep_backfill`, **off** on this box).
- **Retention** (each analysis sweep, and again on an hourly cron):
  - analyzed frames with **no** detections older than **30 days** are removed;
  - if a camera dir exceeds its **5.0 GB** image budget, oldest empty frames go
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

- **[docs/USER-GUIDE.md](docs/USER-GUIDE.md)** — how to use the gallery (end user).
- **[ROADMAP.md](ROADMAP.md)** — what remains, in detail (the live-streaming milestone).
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — how it fits together today, and how earlier versions looked.
- **[API.md](API.md)** — REST endpoints + the SSE event schema (request/response payloads).
- **[DEVELOP.md](DEVELOP.md)** — architecture, configuration, retention, watchdog, and operations reference.
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — host/service/proxy setup and cron install on this box.
