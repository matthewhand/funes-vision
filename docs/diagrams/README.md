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
| [`05-sequence-live-sse.html`](05-sequence-live-sse.html) | Sequence | devs | How do browser, auth, API and the SSE stream interact? | api_server.py:784-1080, nginx.conf, index.html |
| [`06-sequence-recovery.html`](06-sequence-recovery.html) | Sequence | operators | What happens on a stuck sweep, dead unit, or corrupt catalog? | tools/watchdog.sh, systemd/*, analyze_images.py, api_server.py |
| [`07-integrations.html`](07-integrations.html) | DP integration | devs / ops | How do Slack, ntfy and HA MQTT get events, and what leaves the box? | integrations/*, ha_mqtt.py, api_server.py:805 |
| [`08-data-flow.html`](08-data-flow.html) | Data flow | devs / ops | How does frame data move, and where are the retention boundaries? | analyze_images.py, catalog.py, scans.py, settings.example.json |
| [`09-security-boundaries.html`](09-security-boundaries.html) | Architecture | security | What are the trust zones, auth boundaries and secrets? | api_server.py:54-60,85-88,1007-1049, nginx.conf, SECURITY.md |
| [`10-data-model.html`](10-data-model.html) | ER | devs | What do the JSON catalogs contain and how do they relate? | catalog.py, analyze_images.py, taxonomy.py, api_server.py:820 |

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
- `07-integrations` groups IO by provider and draws the dispatcher as the single fan-out
  seam; `ha_mqtt.py` actually publishes from the pipeline, not the API process.
- `10-data-model` shows key fields only, not full schemas, and omits a FK line for
  `retention_log` because retention acts on camera directories rather than an image.
- `06-sequence-recovery` follows the brief that the healthcheck timer probes
  `/api/health`; the repo's `tools/webcam-healthcheck.sh` also guards the gallery
  container (noted as a follow-up).

## Missing / uncertain information

- The exact FTP push interval and the camera firmware's filename template are
  deployment-specific and not pinned in-repo.
- `deep_backfill` and burst-summary defaults differ between `settings.example.json` and
  the reference host; diagrams show the documented defaults.

## Recommended future diagrams

- **State machine**: detection lifecycle (`preliminary` → merged) and image states
  (`analyzed`, `pinned`, `deleted`).
- **Timeline**: a single burst's frame cadence through fast and deep passes.
- **Swimlane**: owner → pipeline → integrations → Home Assistant for one person visit.
