# Architecture

How the Webcam AI Gallery fits together today, and how earlier versions looked.
For configuration and operations detail see [DEVELOP.md](DEVELOP.md); for what's
still planned see [ROADMAP.md](ROADMAP.md).

---

## Current architecture

Three processes cooperate through the shared image directory and a handful of
JSON files. Nothing talks to the internet by default.

```
 Cameras ──(motion JPEGs)──▶ /mnt/models/Webcam2{1,2}
                                  │
            inotifywait -m -e create  (create-index.sh)
                                  │  new file
                                  ▼
                       analyze_images.py  ── fast pass: YOLO (person/car/bird/cat/dog)
                          │   │              deep pass: Gemma vision LLM via local Ollama
                          │   │              consensus → preliminary / verified / disputed
                          │   └──▶ analysis.json, bursts.json, images.json,
                          │         inference_log.json, inference_status.json
                          ▼
                    Integrations (Slack…) on burst/contextual events
                                  │
 Browser ◀── index.html (SPA) ◀── api_server.py (:8190)  ── REST: pins/settings/status/health
                          ▲           │                     SSE /api/events:
                          │           │                       image.new / new-detection /
                          │           │                       detection.preliminary / new-burst
                          └───────────┘  same-origin /api proxy (nginx, basic-auth, TLS)
```

### The pieces

- **`create-index.sh <DIR>`** — runs per camera. An `inotifywait -m` watcher
  reacts to each new snapshot (event-driven, not interval-scanned), regenerates
  the `images.json` catalog, and triggers analysis. Also re-syncs `index.html`
  to the web root each sweep.
- **`analyze_images.py`** — the analysis pipeline. A **fast pass** (YOLO
  `yolov4-tiny`, COCO classes mapped to person/car/bird/cat/dog) labels subjects
  in well under a second; a **deep pass** sends the frame to a local Gemma
  vision LLM (`gemma4:12b`) through Ollama for a reasoned verdict and a short
  caption. A **consensus** rule reconciles the two into the
  preliminary/verified/disputed lifecycle. People/animals jump the queue;
  everything else is backfilled when the box is idle, newest first. Handles
  retention (byte budget + age) and writes the audit trail.
- **`api_server.py` (:8190)** — a stdlib `ThreadingHTTPServer`. REST endpoints
  for pins, delete, settings, integrations, status, and health; an SSE
  `/api/events` stream for live gallery updates. No framework, no build step.
- **`index.html`** — a single-file vanilla-JS SPA (no build). Reads the JSON
  catalogs, renders the Timeline/Objects/All views, and subscribes to
  `/api/events` with graceful fallback to polling.
- **`integrations/`** — a pluggable dispatcher (`notify_burst` / `notify_image`
  / `notify_alert`); Slack is the first provider. Secrets live in a gitignored,
  0600 `integrations.json` that is never synced to the web root.

### Design choices that stuck

- **Local-first, no dependencies.** Standard-library Python HTTP server, a
  single static HTML file, JSON files as the datastore. A standalone Ollama on
  `/mnt/models` does inference; `allow_cloud=false` is the default kill switch.
- **Event-driven over polling.** `inotifywait` reacts to captures immediately
  rather than scanning on a timer.
- **Two-tier inference.** A cheap detector gives instant feedback; the expensive
  LLM (~minutes per image on this box) confirms and narrates when it can. The
  lifecycle (preliminary → verified/disputed) makes that latency visible instead
  of hiding it.

---

## Archive — how it used to look

The project reached the current shape through several distinct architectures.
Each is recorded here so the reasons behind today's design aren't lost.

### Era 0 — Containerized static serving

The earliest form consolidated services into a single root
`docker-compose.yml`, serving captured images as a static directory listing.
No catalog, no client app, no AI — just files behind a web server.
*(`docker-compose.yml` remains in the repo as the container definition.)*

### Era 1 — Client-side SPA + JSON index scraper

The static listing was replaced by a **dynamic single-page app** backed by a
JSON index: a scraper (`create-index.sh`) walked the image directory and emitted
`images.json`, which the SPA rendered with client-side filtering and time-range
controls. This established the "no build step, JSON-as-datastore" pattern still
used today, but there was still no understanding of *what* was in the frames.

### Era 2 — Tiered AI, cloud-capable

The first AI analysis arrived as a **tiered OpenCV + Gemma4** pipeline: a cheap
OpenCV pass plus a Gemma vision model, with execution **gated and event-driven**
to avoid analyzing every frame, **memory-aware local-LLM fallback**, and
bandwidth-saving lazy image loading. Inference could reach a cloud model. This
era proved the two-tier idea but depended on resources/connectivity that didn't
fit a "nothing leaves the box" goal.

### Era 3 — Local-only inference

Inference moved fully in-house: a **standalone Ollama instance on `/mnt/models`**
with the correct model tag, real local calls, and an explicit `allow_cloud`
kill switch flipped **off** by default. Deep passes were batched (a few per
camera per sweep) and the Ollama service was niced so inference never starved
the box. Privacy became a guarantee rather than a preference.

### Era 4 — YOLO fast pass, consensus, and the detection lifecycle

The fast tier became **YOLO** (`yolov4-tiny`), selectable in the UI, replacing
OpenCV as the default detector. A **detector↔LLM consensus mode** was added to
suppress Gemma false positives, and detections gained the
**preliminary/verified/disputed lifecycle** with configurable intervals — the
model still in use. Parked-car gating and idle Gemma backfill date from here.

### Era 5 — Timeline, observability, live updates, integrations

The presentation shifted from a grid of frames to a **Timeline of visits**
(contiguous presence runs, Gemma captions, flipbook playback), made the default
view. An **inference audit trail** and live pipeline status were added for
observability. **SSE** (`/api/events`) began pushing
image.new / new-detection / detection.preliminary / new-burst events to the open gallery,
and a **pluggable integrations module** (Slack first) was introduced. This is
the current era; the remaining live-streaming work (per-image LLM token
streaming, a true pipeline→API push, incremental DOM
patching) is tracked in [ROADMAP.md](ROADMAP.md).
