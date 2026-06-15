# Roadmap

Planned work for the Webcam AI Gallery. Architecture/ops reference: [DEVELOP.md](DEVELOP.md).

---

## Milestone: Live event streaming (push, not poll)

**Goal.** Replace the UI's polling loop with a server→client event stream so the
gallery updates *as things happen* — no waiting on the next sweep/poll tick.

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

- [ ] A persistent stream endpoint on the host API; a client subscribes once
      and receives all three event types with no polling.
- [ ] New images and preliminary detections appear in the open gallery within
      ~1 s of the pipeline acting, with **no full-file refetch** (incremental
      DOM update; `analysis.json` reload becomes a fallback/reconcile, not the
      hot path).
- [ ] The deep LLM pass surfaces live: start, streamed caption text, and final
      verdict — replacing the polled `inference_status.json` + the AI button's
      5 s `renderSystemPanel` poll.
- [ ] Graceful degradation: if the stream drops or the browser lacks support,
      the existing poll path still works (don't delete it — demote it to
      reconnect/fallback). The ℹ status panel shows stream connected/degraded,
      mirroring how it already reports inotify live vs. polling.
- [ ] Survives the multi-process reality: the pipeline
      (`webcam-pipeline@`), the API (`webcam-api`), and the browser are three
      separate processes — events must cross from pipeline → API → client.

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
- **API contract.** Since there's no OpenAPI spec today and this adds a
  long-lived endpoint + an event schema, document the event types + payloads
  (and ideally backfill the 6 existing REST endpoints into the same doc).

### Out of scope (this milestone)

Authentication on the stream, multi-client backpressure tuning, and historical
event replay — note them, defer them.
