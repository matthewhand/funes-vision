# Screenshot tooling

Captures the gallery for the user guide via Playwright (Chromium).
**Default source is the synthetic fixture gallery** — never live camera
footage under `/mnt/models/Webcam21` or `/mnt/models/Webcam22`.

Playwright is **not** a repo dependency — install it into a scratch dir:

```sh
mkdir -p /tmp/pw && cd /tmp/pw && npm init -y >/dev/null
npm install playwright@1.61.0
node node_modules/playwright/cli.js install chromium chromium-headless-shell
```

## Run (fixture-only, safe)

```sh
python3 tools/screenshots/make_fixtures.py   # rebuild stills if needed
bash tools/screenshots/run_shots.sh          # proxy :8899 + shots.js
# PNGs land in /tmp/webcam_shots/

# Copy the named map into the user guide (never glob-copy desktop-*.png):
PUBLISH_GUIDE_IMG=1 bash tools/screenshots/run_shots.sh
```

The proxy refuses to start if `SCREENSHOT_ROOT` points at a live camera
directory. `/api` is stubbed from `fixtures/api/` so pin/delete/settings
cannot mutate the real box.

### What the stub serves

`GET /api/*` answers from the synthetic fixtures, not a hardcoded route table
that can fall behind the SPA:

| Route | Source |
|-------|--------|
| `/api/cameras` | derived from the fixture stills by their leading IP (`tools/screenshots/fixture_api.py`) |
| `/api/taxonomy` | the app's own `taxonomy.py` `payload()`, imported |
| `/api/catalogs` | `images.json` / `analysis.json` / `bursts.json` / `pins.json` grouped per camera |
| `/api/settings`, `/api/status`, `/api/inference_log`, `/api/integrations`, `/api/llm-schema` | `fixtures/api/*.json` |
| `/api/pins` | `fixtures/gallery/pins.json` |
| `/api/health` | literal `{"status":"ok","fixture":true}` |
| `/api/events` | held open, `event: ping` every `SSE_STUB_INTERVAL` |
| every `POST /api/*` | `{"ok":true,"fixture":true}` — never mutates disk |

`fixture_api.py` is shared with `tools/demo/build_demo.py`, so the screenshot
stub and the published demo bundle cannot disagree about the API surface.
`tests/test_stub_api_coverage.py` derives the list of `/api/*` routes from
`index.html` and asserts the stub, the live allowlist, and the demo bundle all
cover it — a new SPA endpoint fails CI instead of silently 404ing behind the
SPA's offline fallback (issue #66).

## Optimize the guide PNGs

Playwright captures are 24-bit RGB PNGs (~0.5-1 MB each). Before committing,
re-encode them to 256-colour PNGs — visually unchanged at guide size and
roughly 63% smaller:

```sh
python3 tools/screenshots/optimize_guide_img.py
```

The script rewrites every `*.png` in `docs/guide/img/` in place using
Pillow's median-cut quantizer (`optimize=True`). Keep the `.png` extensions:
`tests/test_screenshot_proxy.py` asserts `timeline.png` still serves
PNG/JPEG bytes.

## Regenerate the showcase GIFs

`make_gifs.py` builds the animated GIFs embedded in `README.md` and
`docs/USER-GUIDE.md`, plus `tools/demo/demo.gif`. It reads the synthetic
`fixtures/gallery` (stills + `analysis.json`) and assembles frames with
`integrations.media.build_gif` — the same helper the in-app **Download GIF**
uses — so the frame cap, widths and `optimize=True` palette match the export.

```sh
python3 tools/screenshots/make_gifs.py
```

This rewrites, in place and with no network access:

| Output | Frames | Source |
|--------|--------|--------|
| `docs/guide/img/timeline-flipbook.gif` | whole gallery | `images.json` order, oldest first |
| `docs/guide/img/visit-player.gif` | person + dog visits | `analysis.json` flags, oldest first |
| `tools/demo/demo.gif` | person + dog visits | same, 320 px demo width |

Frames are sorted ascending by the filename clock (the in-app flipbook's
order) and the hold is pinned to the 2.5 fps flipbook rate, so the clips are
reproducible instead of following the fixture gaps. Widths are tuned
(`TIMELINE_WIDTH`/`VISIT_WIDTH`/`DEMO_WIDTH`) so each file stays well under
~1.5 MB. The script prints frame counts, delays and byte sizes and exits
non-zero if any output is not actually animated (Pillow frame count `< 2`).
`--out DIR` and `--no-demo` are available for scratch runs, and `--check`
validates the already-committed GIFs read-only (animated, one uniform hold,
under budget) without rewriting them, and byte-compares them against the
`sha256` manifest in `make_gifs.py` (re-pin Pillow first, then copy the digests
the write run prints into `COMMITTED_SHA256`).

## Env

| Var | Default | Meaning |
|-----|---------|---------|
| `SCREENSHOT_ROOT` | `tools/screenshots/fixtures/gallery` | Static gallery tree |
| `SCREENSHOT_INDEX` | repo `index.html` | Overlay working-copy UI |
| `SCREENSHOT_API` | `stub` | `stub` (safe) or `live` (`:8190`) |
| `SCREENSHOT_PORT` | `8899` | Local origin |
| `SHOTS_URL` / `SHOTS_OUT` | `:8899` / `/tmp/webcam_shots` | Playwright |

Published guide shots **must** use `SCREENSHOT_API=stub` (already the
default). The stub holds `GET /api/events` open and emits `event: ping`
every ~2s (capped at 120s) so Live/status captures stay Live.
`SCREENSHOT_API=live` is dev-only (stub is the default), 404s
`/api/events`, and forwards only an explicit `/api/*` allowlist — any
other path is refused with 403. It is not used for published shots.

## Gotchas

- `page.screenshot()` on heavy views is slow, not hung. Timers are frozen
  and CSS animations (including `::before`/`::after`) are disabled.
  `shots.js` waits for `domcontentloaded` + a gallery selector — never
  `networkidle` (the SSE stub keeps `/api/events` open).
- `PUBLISH_GUIDE_IMG=1` copies a fixed name map into `docs/guide/img/`
  (`timeline.png`, `objects.png`, …). Do not `cp /tmp/webcam_shots/*.png`
  there — those files are still named `desktop-01-…`.
- Headless: use `documentElement.clientWidth` for viewport math; never
  `pkill headless_shell` right before launch (ETXTBSY).
- Curated outputs for the user guide live in `docs/guide/img/`
  (`timeline.png`, `objects.png`, `all-grid.png`, `lightbox.png`, …).
  `docs/img/` is gitignored (old live-camera captures — never commit).
