# Screenshot tooling

Captures the live SPA for the README/user guide via Playwright (Chromium).
Playwright is **not** a repo dependency — install it into a scratch dir so the
no-build app stays dep-free:

```sh
mkdir -p /tmp/pw && cd /tmp/pw && npm init -y >/dev/null
npm install playwright@1.61.0
node node_modules/playwright/cli.js install chromium chromium-headless-shell
```

## Run

The SPA calls a same-origin `/api`, and the public site is behind nginx basic
auth — so we serve the deployed gallery and reverse-proxy `/api` to the real
API (`:8190`) under one local origin, then point Playwright at it.

```sh
python3 tools/screenshots/proxy.py &          # serves Webcam21 + /api on :8899
cd /tmp/pw && node /path/to/repo/tools/screenshots/shots.js   # writes /tmp/webcam_shots/*.png
```

## Gotchas (why the script is shaped the way it is)

- **Slow capture, not a repaint.** `page.screenshot()` on heavy views is slow,
  not hung. Investigated: there is NO `requestAnimationFrame` loop in the app;
  the only animation is a tiny `live-dot` pulse. The script freezes CSS
  animations **including `::before`/`::after` pseudo-elements** (the universal
  selector alone misses them, e.g. `.skeleton-card::after`), hides spinners,
  kills JS timers (`freezeTimers`), pre-scrolls to load lazy thumbnails
  (`preloadLazy`), and passes `animations:'disabled'`.
- The **default Timeline view** renders up to 300 visit rows with thumbnails,
  so `captureScreenshot` takes ~12–13 s — finite, not a hang. The screenshot
  `timeout` is 25 s for this reason; a shorter timeout falsely looks like a
  "Timeline never settles" bug (it isn't one).
- Headless: use `documentElement.clientWidth` (not `window.innerWidth`) for the
  viewport; never `pkill headless_shell` right before launching (ETXTBSY).
- Curated outputs live in `docs/img/`.
