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
- [x] New images and preliminary detections appear in the open gallery within
      ~1 s of the pipeline acting, with **no full-file refetch**. SSE events now
      patch in-memory state (`mergeNewImage` for `image.new`, `detectionEntry`
      for detections — optimistic, reconciled) and re-render from it;
      `analysis.json`/`images.json` reload is demoted to the 30 s poll + onerror
      fallback. *(Re-renders from state rather than per-node DOM diffing — the
      costly network refetch is gone; finer node-level patching is a possible
      future optimisation.)*
- [~] The deep LLM pass surfaces live: start, streamed caption text, and final
      verdict. *(Plumbing built: the burst/context call now **streams** via
      `_ollama_chat_stream` (stream:true), writing the building caption into
      `inference_status.json.partial`, which `/api/status` exposes and the ℹ
      panel shows live ("Analyzing now …" + the caption in progress). Still
      polled at the panel's cadence, not pushed as `analysis.token` SSE events;
      and **live validation is owner-gated** — deep passes are OFF, so this runs
      only once Gemma/a vision model is enabled. Pure parts (`parse_chat_chunk`,
      stream accumulation) are unit-tested against a mock.)*
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

**Migration plan (prepared — do NOT execute without owner sign-off).** Concrete
steps, in safe order:

1. *Resolver (code, reversible, no files move).* Add `paths.py` exporting
   `config_dir()` / `state_dir()` / `runtime_dir()`, each `env override → XDG
   dir → legacy BASE_DIR`. Replace the three independent roots —
   `api_server.py:27` and `analyze_images.py:22` (`BASE_DIR`) and
   `integrations/__init__.py:21` (`ROOT`) — with calls to it. With no `XDG_*`
   set, every path is byte-identical to today, so this can ship and bake first.
2. *systemd env (needs sudo).* In `systemd/webcam-api.service` and
   `systemd/webcam-pipeline@.service`, add under `[Service]`:
   `Environment=XDG_CONFIG_HOME=/home/user/.config`
   `Environment=XDG_STATE_HOME=/home/user/.local/state`
   (or app-specific dirs). Both units must agree — they share the files.
   `WEBCAM_TZ=Australia/Sydney` can land in the same edit.
3. *One-time migration (touches secrets — owner runs).* `mkdir -p
   $XDG_CONFIG_HOME/webcam $XDG_STATE_HOME/webcam`; move **config**
   (`settings.json`, `integrations.json` — mode 600) and **state**
   (`inference_log.json`, `inference_status.json`, `retention_log.json`,
   `alert_state.json`, `integrations_state.json`); leave the **data plane**
   (`analysis/bursts/images/pins.json`, `thumbs/`, JPEGs) in the web roots.
   `pins.json` keeps its canonical copy in state **and** the synced web-root
   copy. `/tmp/webcam_analysis.{lock,lastrun}` → `$XDG_RUNTIME_DIR/webcam/`.
4. *Verify.* `GET /api/status` + `/api/health` unchanged; Slack secrets still
   load; a sweep still writes state to the new dir. Roll back by clearing the
   `XDG_*` env (resolver falls back to `BASE_DIR`).

---

## Considered: Playwright browser tests (deferred)

**Goal.** Add real DOM/integration coverage that the current harness can't give
(it tests pure helpers, not rendering, events, or navigation).

**Decision: deferred.** It would add value in principle, but the cost outweighs
it for this project right now:

- **Breaks the stack's ethos.** The app and its tests are deliberately
  dependency-free and build-free (stdlib Python + `node` asserts, no
  `package.json`/`node_modules`). Playwright pulls in a node dependency tree +
  a browser binary — a different maintenance model.
- **Disk.** Playwright + a browser binary is ~0.5–1 GB, installed under the
  user home on the **root** disk, which runs tight (it has hit ~88–96% used).
- **Marginal added coverage.** The SPA's risky logic (timezone/DST parsing,
  detection lifecycle, filtering, all the time/duration formatters) is already
  extracted into pure helpers with node-assert coverage; what's left for a
  browser is mostly layout/wiring, lower-risk and visually obvious.

**Revisit when** DOM-level regressions actually bite, the project grows past one
box, or the no-deps constraint is relaxed. Until then the harness stays
node-assert + `unittest` + `node --check` on the inline scripts.

---

## Live event streaming milestone — status (2026-06-18)

Substantially complete. **Done:** SSE `/api/events` (image.new / detection.preliminary
/ new-detection / new-burst), graceful fallback + a connected/degraded indicator,
**incremental in-memory updates with no full-file refetch** (A1), and **streamed
LLM context captions** surfaced live in the ℹ panel (A2 plumbing). **Deferred:**
A3 (true pipeline→API push to replace the 3 s mtime bridge) — the mtime poll
works and a thread-safe pub/sub rewrite of the live core isn't cleanly/safely
testable autonomously; revisit when sub-second latency is actually needed.
**Owner-gated:** live token validation + `analysis.token` SSE push need deep
passes ON with a vision model.

---

## UX / Quality backlog (from the 2026-06-18 multi-agent critique)

Concrete, prioritized findings from a parallel review of every surface. P1 =
bug / broken / misleading / security; P2 = notable UX or a11y friction; P3 =
polish. All fixes are additive, no-dep, vanilla JS/CSS. Built items are checked off.

### P1 — correctness / security / blocking a11y
- [x] **HTML-escape all server/model strings rendered via `innerHTML`** — added a
      pure `escapeHtml()`; wrapped the streamed `inference.partial` caption, the
      "Analyzing now" image/model/trigger, the audit-trail labels/trigger/image
      (href via `encodeURI` + `rel=noopener`), camera names, and the player's
      visit label/type. *(Shipped — feat/escape-html.)*
- [x] **Keyboard-operate the gallery** — cards are now `role="button"`,
      `tabindex=0`, Enter/Space opens the lightbox, with a human `aria-label`
      (`cardAriaLabel`) + a `:focus-visible` ring. Card alt/caption/labels are now
      escaped too. *(Shipped — feat/card-a11y.)*
- [ ] **Keyboard-operate the custom controls** — sidebar date items, chart bars,
      blacklist/alias/Slack toggles, the Live toggle are click-only `<div>`s. Add
      `role`/`tabindex`/`aria-*` + Enter-Space handlers (or convert to `<button>`).
- [x] **Dialog semantics + focus management** — lightbox + flipbook player are
      now `role="dialog" aria-modal`, move focus in on open, trap Tab within
      (pure `tabWrap` + `trapFocus`), and restore focus to the opener on close.
      *(Shipped — feat/dialog-focus.)*
- [x] **Mobile touch targets** — `@media (pointer:coarse)` bumps the card action
      buttons, view toggles and toggle rows to >=44px. *(Shipped — feat/a11y-css.)*
- [x] **`ongoing` visit is misleading** — now gated on recency via pure
      `isRecent()` (end frame within 10 min of now); older last visits show their
      duration + the done icon. *(Shipped — feat/ongoing-recency.)*
- [x] **"Show all images" doesn't clear all filters** — replaced with a "Clear
      all filters" escape (shown whenever any filter is active, any tab) wired to
      `resetAllFilters()` (pure `clearedFilters()` + DOM resync). *(Shipped — feat/show-all-reset.)*
- [ ] **Player robustness** — fetch/`onerror` stale-frame closure (capture `idx`),
      GIF-download race after close (AbortController + `overlay.isConnected` guard),
      Space double-toggle when a button is focused.
- [x] **Detection badges encode meaning by colour only** — each badge is now
      `role="img"` with an `aria-label` spelling out the state (pure `badgeLabel`:
      "person, unconfirmed" / "dog, disputed"); the decorative icon + glyph are
      `aria-hidden`. *(Shipped — feat/badge-a11y.)*

### P2 — notable UX / a11y / correctness
- [ ] **Cross-midnight visits** mislabel to the start date and silently cap at 60
      frames; show both dates + a "(first 60 frames)" note.
- [x] **Insights stats are "loaded frames", not totals** — relabeled "N loaded
      frames" (+ tooltip) so it no longer contradicts the on-disk count. *(Shipped — feat/panel-accuracy.)*
- [x] **Backfill bar excludes `unanalyzed`** — `backfillProgress` now folds
      `unanalyzed` into pending, so the bar can't read 100% while frames are
      still queued. *(Shipped — feat/panel-accuracy.)*
- [x] **Stream "live" never goes stale** — the heartbeat is now a named `ping`
      SSE event the client tracks (`lastPingTs`); the panel shows "stale (no
      signal)" when pings stop (a silent stall), distinct from a quiet period.
      *(Shipped — feat/stream-stale.)*
- [x] **Search predicate diverges** — unified into one pure `matchesSearch()`
      (filename + date/time/type/caption) used by both the grid and the charts.
      *(Shipped — feat/search-unify.)*
- [ ] **Reduced-motion**: the flipbook auto-plays on open regardless; gate initial
      `play()` behind `prefers-reduced-motion`.
- [ ] **Hidden-but-active filters** — hiding filter chips while a chip is active
      leaves an invisible applied filter with no clear affordance; show an inline
      "N active ✕".
- [ ] **Object chips lack counts/contrast** — add per-label frequency counts.
- [ ] **Search placeholder undersells** caption/label search — reword.
- [ ] **Status panel re-renders every 5s** destroying audit-trail scroll/focus;
      skip refresh while scrolled/hovered, or diff-update.
- [x] **Focus-visible** ring — global `:focus-visible` outline now covers all
      interactive controls (buttons, links, inputs, role=button cards, chips,
      tabs, sidebar/blacklist items). *(Shipped — feat/a11y-css.)*
- [ ] **Lightbox/player control bars overflow on phones** — allow wrap / hide
      redundant zoom buttons under 768px.
- [ ] **300-visit / list-view caps** silently truncate with no "showing first N"
      notice; `stats-label` goes stale on the Timeline tab.
- [ ] **`<img alt>` is the raw filename** everywhere — derive a human alt from
      parsed metadata.

### P3 — polish / design system
- [ ] **Design tokens**: unify radii, a 4px spacing scale, one accent (three blues
      today), and one badge recipe (tinted bg + bordered, like disputed/potential)
      — replace ad-hoc per-element values with `:root` vars.
- [ ] **`#stats-label` has no CSS rule** (renders at body default, louder than the
      toolbar); make it `0.78rem` secondary + tabular-nums.
- [ ] **Toast looks like a primary CTA** (solid accent); restyle as a surface
      notification, reserve accent for the icon.
- [x] **`fmtDur`/`formatSeconds` cap at minutes** — now emit `Hh Mm` past 3600s
      (e.g. "1h 13m" not "73m 12s") for long ages/inferences. *(Shipped — feat/format-hours.)*
- [ ] **Status numbers** lack locale grouping; sub-1GB cameras show "0.0GB".
- [ ] **Empty-state polish**: distinct "Analyzing…" vs "no visits" vs error; "no
      data yet" placeholder for the all-zero sparkline; reserve red for real faults
      (the activity pill is alarming-red for benign events).
- [ ] **Elevation hierarchy inverted** — cards lift dramatically on hover while
      their container panels are flat; add a subtle resting shadow, soften hover.

*Build order: P1 security/correctness first (HTML-escape, ongoing, show-all), then
the a11y cluster, then P2/P3. Each ships TDD + squash-merged.*
