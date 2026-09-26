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
