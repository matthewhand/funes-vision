# Roadmap

Planned work for the Webcam AI Gallery. Architecture/ops reference:
[DEVELOP.md](DEVELOP.md) · evolution & current design: [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Milestone: Live event streaming (push, not poll)

**Goal.** Replace the UI's polling loop with a server→client event stream so the
gallery updates *as things happen* — no waiting on the next sweep/poll tick.

### Status (2026-06-17): partially built

The transport and the first two event types exist; the headline LLM-streaming
piece and a true push bridge do not.

- **Done.** An SSE endpoint `/api/events` (stdlib `BaseHTTPRequestHandler`,
  `text/event-stream`, `X-Accel-Buffering: no`, heartbeat) emits **`image.new`**,
  **`new-detection`**, **`detection.preliminary`**, and **`new-burst`**. The SPA
  subscribes once via `EventSource` and falls back to polling when SSE is
  unsupported or the connection can't be established. (`image.new` is keyed off
  a frame first appearing in `analysis.json` — i.e. right after the fast pass —
  since the raw inotify ingest lives in the pipeline process, not the API.
  Preliminary detections matter most while deep passes are off: they're the only
  live detections then, since nothing reaches a verified verdict.)
- **Not yet.** The bridge is a **3 s file-mtime poll** inside the API (it diffs
  `analysis.json` / `bursts.json` on change), not a pipeline→API push, so
  latency is ~3 s. The headline **`analysis.llm`** live stream is missing — the
  Ollama deep-pass call is non-streaming, so surfacing "AI is looking at this…"
  with the caption typing in remains a prerequisite sub-task. The SPA also still
  `loadData()`-refetches on each event (coalesced for `image.new`) rather than
  patching the DOM incrementally.

### Why

Today the SPA polls (`index.html`): `renderSystemPanel` every 5 s, plus
cache-busted refetches of `analysis.json` (~850 KB), `bursts.json`,
`images.json`, `pins.json`, and `/api/status` · `/api/inference_log` ·
`inference_status.json`. That means up to several seconds of latency, repeated
re-download of large files for tiny deltas, and — worst for UX — the **deep LLM
pass (~5.5 min/image on this box) is invisible until it finishes and the next
poll lands**. The pipeline already knows the moment each thing happens; the UI
just can't hear it yet.

### Events to stream (priority order)

1. **`image.new`** — a motion snapshot was ingested. Source: the
   `inotifywait -m -e create` watcher in `create-index.sh`. Payload: camera,
   filename, timestamp. UI: prepend to the All/Timeline view live.
2. **`detection.preliminary`** — the fast detector (YOLO) produced labels.
   Source: the fast pass in `analyze_images.py`. Payload: filename, labels,
   boxes. UI: render the amber `label?` badge immediately.
3. **`analysis.llm`** — *the headline feature.* The local vision LLM
   (`gemma4:12b`) is reasoning about an image, streamed **as it happens**, not
   only on completion. Source: the deep pass in `analyze_images.py` (the same
   place that writes `inference_status.json`). Emit at minimum:
   `analysis.started` (image, model, trigger — the current `inference_status.json`
   shape) → `analysis.token`/partial caption chunks if the Ollama call is
   streamed → `analysis.done` (verdict: verified / disputed, final caption).
   UI: live "AI is looking at this…" with the caption typing in, and the
   detection badge resolving preliminary → verified/disputed in place.

### Acceptance criteria

- [x] A persistent stream endpoint on the host API; a client subscribes once.
      *(Done for `new-detection` / `new-burst`; still only 2 of the 3 event
      types, and the SPA keeps polling for everything else.)*
- [ ] New images and preliminary detections appear in the open gallery within
      ~1 s of the pipeline acting, with **no full-file refetch** (incremental
      DOM update; `analysis.json` reload becomes a fallback/reconcile, not the
      hot path). *(Partial: `image.new` + `detection.preliminary` now fire (~3 s
      mtime bridge) and wake the gallery, but via a full `loadData()` refetch
      (coalesced), not an incremental DOM patch.)*
- [ ] The deep LLM pass surfaces live: start, streamed caption text, and final
      verdict — replacing the polled `inference_status.json` + the AI button's
      5 s `renderSystemPanel` poll. *(Not started; needs a streaming Ollama
      call first.)*
- [x] Graceful degradation: if the stream drops or the browser lacks support,
      the existing poll path still works (kept as the fallback). The ℹ status
      panel now shows a **Live updates: live (SSE connected) / polling (stream
      offline)** indicator next to the inotify trigger line.
- [x] Survives the multi-process reality: the pipeline
      (`webcam-pipeline@`), the API (`webcam-api`), and the browser are three
      separate processes. *(Satisfied via the file-based bridge — the API reads
      the JSON files the pipeline writes; a push-based bus is still the goal.)*

### Implementation notes (decide during design)

- **Transport.** The ask is "websocket," but every event here is one-way
  server→client, which is exactly **Server-Sent Events (SSE)**' sweet spot —
  and SSE drops cleanly onto the existing stdlib `BaseHTTPRequestHandler`
  (`text/event-stream`, a long-lived `do_GET`, `ThreadingHTTPServer` already
  in place), whereas raw WebSocket needs the HTTP-upgrade handshake + frame
  codec or a new dependency (`websockets`/`simple-websocket`). Recommend SSE
  unless bidirectional control (e.g. client-driven "analyze this now") is also
  wanted — then WebSocket earns its keep. Pick one in design; this milestone
  is transport-agnostic on the event contract.
- **Pipeline → API bridge.** `create-index.sh` / `analyze_images.py` run in a
  different process from `api_server.py`. Generalize the existing
  `inference_status.json` poll-bridge into an event bus: simplest viable path
  is the pipeline POSTing events to a local `/api/_event` ingest on `:8190`
  that fans out to subscribed streams; alternatives are a FIFO/unix socket the
  API tails, or Redis pub/sub if a dep is acceptable.
- **LLM streaming.** Getting `analysis.token` requires calling Ollama with
  `stream: true` and forwarding chunks; if the current call is non-streaming,
  that's a prerequisite sub-task.
- **API contract.** ✅ Done — [API.md](API.md) documents every REST endpoint's
  request/response payloads plus the SSE event schema (event names + JSON
  payloads + when each fires). New event types (`analysis.llm`) should be added
  there as they ship. (A formal OpenAPI spec is still optional/deferred.)

### Out of scope (this milestone)

Authentication on the stream, multi-client backpressure tuning, and historical
event replay — note them, defer them.

---

## Considered: XDG Base Directory paths (not scheduled)

**Goal.** Move the server-side **control plane** off the repo checkout and onto
XDG conventions, so secrets and mutable state aren't interleaved with source.

**Scope.** Control plane only — config (`settings.json`, `integrations.json`) →
`$XDG_CONFIG_HOME/webcam/`; server-only state (`inference_log.json`,
`inference_status.json`, `retention_log.json`, `alert_state.json`,
`integrations_state.json`) → `$XDG_STATE_HOME/webcam/`; `/tmp` markers →
`$XDG_RUNTIME_DIR/webcam/`. The **data plane** (`images.json`, `analysis.json`,
`bursts.json`, `pins.json`, `thumbs/`, JPEGs) stays in the nginx web roots —
it's web-served and belongs with the camera data, so XDG doesn't apply.

**Approach.** A shared path resolver (`env override → XDG dir → legacy
`BASE_DIR` fallback`) imported by all three entry points (`api_server.py`,
`analyze_images.py`, `integrations/__init__.py`) — default behaviour unchanged
unless `XDG_*`/env is set. Then relocate config + state behind it.

**Why it's only "considered".** On a single-box homelab the payoff is modest
(mainly: secrets/state out of the checkout). The cost is real: it touches three
processes, needs consistent env in the two systemd units, and a one-time file
migration. It also moves `integrations.json` (secrets) and edits systemd
(sudo), so it's a deliberate, reviewed change — not autonomous-loop work.
`pins.json` is dual-purpose (server state **and** a web-served copy) and would
keep a synced web-root copy. Step 1 (the resolver with legacy fallback, no
files moved) is fully reversible and could land first.
