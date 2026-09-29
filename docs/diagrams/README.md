# funes-vision — diagram set

A coherent visual documentation set for **funes-vision**, a local-first two-stage
computer-vision sentry for camera motion stills. Every diagram is derived from the
repository (source refs in each footer) and rendered as one HTML file with inline
SVG, plus the shared local `fonts.css` — no build step, no network, no CDN.

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
| [`14-frontend-internals.html`](14-frontend-internals.html) | Architecture | devs | Which group of the A–S sections in the one-file SPA do I open? | index.html:2-121,3126-8944, api_server.py:1509-2034, DEVELOP.md:469-497 |
| [`15-test-ci-gates.html`](15-test-ci-gates.html) | Architecture | devs | What has to be green before a change lands? | .github/workflows/ci.yml, tests/run.sh:13-34, tools/screenshots/a11y_audit.js, requirements-dev.txt:19-23 |
| [`16-asset-manifest.html`](16-asset-manifest.html) | Data flow | devs / ops | Which four lists decide what ships, and which test keeps them in step? | tests/test_static_asset_manifest.js, create-index.sh:48-58, tools/deploy-webroot.sh, tools/demo/build_demo.py, tools/screenshots/proxy.py:229-243 |

That is **16 diagrams**. `_template.html` is the shared dark skeleton;
`_conventions.md` holds the token table and connector rules. Neither is a
deliverable in itself.

---

## Fonts

The three faces the set uses — **Instrument Serif** (page titles), **Geist**
(node names) and **Geist Mono** (technical sublabels) — are **vendored in this
repo**, not loaded from a font CDN:

```
docs/diagrams/
├── fonts.css                              # the only @font-face declarations
├── fonts/
│   ├── Geist-Variable.woff2               #  69,760 B  wght 100–900
│   ├── GeistMono-Variable.woff2           #  71,596 B  wght 100–900
│   ├── InstrumentSerif-Regular.woff2      #  27,316 B
│   ├── InstrumentSerif-Italic.woff2       #  28,012 B
│   ├── OFL-Geist.txt                      # SIL OFL 1.1, both Geist files
│   ├── OFL-InstrumentSerif.txt            # SIL OFL 1.1, both Instrument Serif files
│   └── README.md                          # provenance, checksums, license
└── <diagram>.html                         # <link href="fonts.css" rel="stylesheet">
```

Every diagram loads that one stylesheet with a **relative** path, so opening a
diagram from disk, over a LAN, or on an air-gapped host makes **zero third-party
requests**. Both families are SIL Open Font License 1.1, which permits
redistribution provided the license travels with the files — the `OFL-*.txt`
files are part of the vendored set and must not be deleted.

This replaced a `<link>` to `https://fonts.googleapis.com/css2` that every
diagram carried, which contradicted the project's own no-remote-CDN rule (see
[issue #44](https://github.com/matthewhand/funes-vision/issues/44)). A system
font stack was considered first and rejected: it would have made headings render
in whatever serif the reader happens to have, so the 16 diagrams would stop
looking like a set. Self-hosting is a one-time ~192 KB and keeps the design
byte-for-byte identical to the CDN rendering.

**To add or change a face:** put the woff2 in `fonts/`, declare it in
`fonts.css`, and record its provenance, byte count, `sha256` and OFL text in
[`fonts/README.md`](fonts/README.md). Do not inline `@font-face` into a
diagram's `<style>` — `self_check.py` rejects non-fragment `url()`, so the
faces have to stay in the sibling stylesheet.

`tests/test_no_remote_cdn.js` fails the build if a diagram or `fonts.css` gains
a remote host, if a diagram stops linking `fonts.css`, if a referenced woff2 is
missing or empty, or if an `OFL-*.txt` is removed.

---

## How to edit or regenerate

1. Read `_conventions.md` and copy `_template.html`.
2. Keep the connector rules: right-angle elbows only, masked arrow labels with a 6–10px
   gap, zones → arrows → nodes draw order, one legend strip at the bottom.
3. Replace the `[diagram-slug]` token in the `<title>`/`<desc>` IDs and fill both.
4. Keep `<link href="fonts.css" rel="stylesheet">` in `<head>`. Do not swap it for
   a remote stylesheet, and do not inline `@font-face` (see **Fonts** above).
5. Add a row to the **Diagram index** table, and any assumption the diagram bakes in
   to **Assumptions and limitations** below.
6. Validate:

   ```bash
   python3 ~/.claude/skills/diagram-design/scripts/self_check.py docs/diagrams/<file>.html
   bash tests/run.sh
   ```

Render/preview by opening the HTML file in any modern browser — the diagrams are
plain local files, so this works with no network at all. There is no build step;
raster exports, if ever needed, should be produced from the inline SVG, not
hand-made.

To re-skin the whole set, edit the profile at
`~/.diagram-design/profiles/funes-vision.md` (or change the `.diagram-design`
marker). If that changes the *typeface*, update `fonts.css` and `fonts/` too —
the profile's font names are only a wish until a woff2 backs them.

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
security, data model, state machine, timeline, swimlane, frontend internals, CI
gates, asset manifest) is complete. Possible additions:

- **Quadrant or backlog chart**: retention pressure vs. detection value per camera.
- **Dependency graph**: Python module fan-in across `analyze_images.py`, `api_server.py`,
  `catalog.py`, `scans.py`, `taxonomy.py`. [`14-frontend-internals.html`](14-frontend-internals.html)
  already maps the client side of the same question (which group of the A–S sections
  owns a job); this would be the server-side equivalent, which nothing covers yet.
