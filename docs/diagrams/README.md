# funes-vision — diagram set

A coherent visual documentation set for **funes-vision**, a local-first two-stage
computer-vision sentry for camera motion stills. Every diagram is derived from the
repository (source refs in each footer) and rendered as a single self-contained HTML
file with inline SVG.

Skin: the `diagram-design` skill, project profile **`funes-vision`** (dark, matched to
the app UI). The profile is selected by the `.diagram-design` marker at the repo root
(`profile: funes-vision`).

---

## Diagram index

| File | Type | Audience | Question it answers | Primary sources |
|------|------|----------|---------------------|-----------------|
| [`01-hero-overview.html`](01-hero-overview.html) | Architecture | everyone / README | What is it, who uses it, and where is its boundary? | README.md, ARCHITECTURE.md, api_server.py, analyze_images.py |
| [`02-runtime-architecture.html`](02-runtime-architecture.html) | Architecture | devs / operators | What runs where, and which boundaries separate them? | api_server.py, analyze_images.py, systemd/*, nginx.conf |
| [`03-deployment.html`](03-deployment.html) | Deployment | operators | How is it deployed on one host — ports, volumes, containers, cron? | docker-compose.yml, systemd/*, nginx.conf, tools/webcam-compose-run.sh |
| [`04-sequence-ingest-analysis.html`](04-sequence-ingest-analysis.html) | Sequence | devs | How does a still move from FTP to a catalogued detection? | create-index.sh, analyze_images.py, scans.py, taxonomy.py |
| [`05-sequence-live-sse.html`](05-sequence-live-sse.html) | Sequence | devs | How do browser, auth, API and the SSE stream interact? | api_server.py:684-720,1003-1147, pipeline_events.py, nginx.conf, index.html |
| [`06-sequence-recovery.html`](06-sequence-recovery.html) | Sequence | operators | What happens on a stuck sweep, dead unit, or corrupt catalog? | tools/watchdog.sh, systemd/*, tools/webcam-healthcheck.sh, analyze_images.py, api_server.py |
| [`07-integrations.html`](07-integrations.html) | DP integration | devs / ops | How do Slack, ntfy and HA MQTT get events, and what leaves the box? | integrations/*, ha_mqtt.py, api_server.py:805 |
| [`08-data-flow.html`](08-data-flow.html) | Data flow | devs / ops | How does frame data move, and where are the retention boundaries? | analyze_images.py, catalog.py, scans.py, settings.example.json |
| [`09-security-boundaries.html`](09-security-boundaries.html) | Architecture | security | What are the trust zones, auth boundaries and secrets? | api_server.py:60,79-87,684-708, nginx.conf, SECURITY.md |
| [`10-data-model.html`](10-data-model.html) | ER | devs | What do the JSON catalogs contain and how do they relate? | DEVELOP.md:449-474, analyze_images.py:1522,960,2286, create-index.sh:22, api_server.py:357 |
| [`11-detection-states.html`](11-detection-states.html) | State machine | devs | How does an image move from unanalyzed to merged, pinned or deleted? | catalog.py:13-58, analyze_images.py:838,981,1201,1760 |
| [`12-burst-timeline.html`](12-burst-timeline.html) | Timeline | devs / ops | What happens, and when, within one visit — and how long does the deep pass lag? | analyze_images.py, api_server.py:1003-1147, pipeline_events.py, integrations/media.py:20 |
| [`13-visit-swimlane.html`](13-visit-swimlane.html) | Swimlane | devs / ops | Which lane owns each handoff for one person visit, camera to notification? | create-index.sh:10, analyze_images.py:2120-2294, api_server.py, index.html |

`_template.html` is the shared dark skeleton; `_conventions.md` holds the token table and
connector rules. Neither is a deliverable in itself.

---

## How to edit or regenerate

1. Read `_conventions.md` and copy `_template.html`.
2. Keep the connector rules: right-angle elbows only, masked arrow labels with a 6–10px
   gap, zones → arrows → nodes draw order, one legend strip at the bottom.
3. Replace the `[diagram-slug]` token in the `<title>`/`<desc>` IDs and fill both.
4. Validate:

   ```bash
   python3 ~/.claude/skills/diagram-design/scripts/self_check.py docs/diagrams/<file>.html
   ```

Render/preview by opening the HTML file in any modern browser. There is no build step;
raster exports, if ever needed, should be produced from the inline SVG, not hand-made.

To re-skin the whole set, edit the profile at
`~/.diagram-design/profiles/funes-vision.md` (or change the `.diagram-design` marker).

---

## Assumptions and limitations

- Diagrams are **dark-only** by design; the profile's light column intentionally mirrors
  the dark one.
- Zone shapes are illustrative containers; the real loopback trust is enforced by the API
  bind address, not a network control.
- `03-deployment` assumes standard ports (FTP `:21`, MQTT `:1883`) and the documented
  defaults (`settings.example.json`: 30 days, 5 GB per camera, 20% persist budget).
- `04-sequence-ingest-analysis` merges the YOLO fast pass and the Ollama deep pass into
  one lifeline to stay within the sequence budget; both are named in the messages.
- `08-data-flow` conditions the deep pass on the real rule: any detector hit
  whose only True labels are gate-ignored (default `car`) is persisted as
  `_llm_skip=no_trigger` instead of queued, so person, dog, cat, bird and a car
  sharing the frame all reach the LLM.
- `05-sequence-live-sse` shows the API tailing `events.jsonl`; catalog-mtime
  diffing is the fallback branch, and the 503 client cap is the failure exit.
- `07-integrations` groups IO by provider and draws the dispatcher as the single fan-out
  seam; `ha_mqtt.py` actually publishes from the pipeline, not the API process.
- `10-data-model` transcribes the real key sets from the writers
  (`retention_log` = `{ts, dir, count, bytes_freed}`, `bursts` entries =
  `{summary, images[]}`, `inference_log` = `{image, model, trigger, started,
  duration_s, labels, ok}`) rather than showing invented columns, and it omits a
  FK line for `retention_log` because retention acts on camera directories
  rather than an image. `images.json` and `pins.json` are flat arrays of
  filenames, so the `images` box labels the image each row stands for and says so
  on the canvas.
- `06-sequence-recovery` draws the 60s healthcheck timer against the **gallery
  container**: `tools/webcam-healthcheck.sh` runs `docker ps` and then
  `curl http://127.0.0.1:8180/`, and restarts the container with
  `compose up -d gallery` if both miss. It does **not** call `/api/health` — that
  is the cron watchdog's probe (`tools/watchdog.sh`). The two are drawn to
  separate lifelines so the distinction survives.
- `11-detection-states` folds the catalog's `no_trigger` into `detector-only`;
  idle backfill is off by default and set by the `deep_backfill` setting key
  (module constant `DEEP_BACKFILL`) — noted in a card rather than drawn.
- `12-burst-timeline` uses an illustrative ~2 s frame cadence; the ~40 s deep-pass
  latency is the reference 4-core box. Two axis breaks mark compressed time.
  The SSE leg is the pipeline's `events.jsonl` bus (1 s tail), not a 3 s mtime
  poll; `ping` beats every 15 s.
- `13-visit-swimlane` draws the Integrations handoff from the API/SSE step for narrative
  order, though `analyze_images.py` actually fires Slack/HA MQTT from the pipeline.

## Missing / uncertain information

- The exact FTP push interval and the camera firmware's filename template are
  deployment-specific and not pinned in-repo.
- `deep_backfill` and burst-summary defaults differ between `settings.example.json` and
  the reference host; diagrams show the documented defaults.

## Recommended future diagrams

The core set (hero, architecture, deployment, sequences, integrations, data flow,
security, data model, state machine, timeline, swimlane) is complete. Possible additions:

- **Quadrant or backlog chart**: retention pressure vs. detection value per camera.
- **Dependency graph**: Python module fan-in across `analyze_images.py`, `api_server.py`,
  `catalog.py`, `scans.py`, `taxonomy.py`.
