# Screenshot tooling

Captures the gallery for the user guide via Playwright (Chromium), and audits
it for accessibility and visual defects.
**Default source is the synthetic fixture gallery** — never live camera
footage under `/mnt/models/Webcam21` or `/mnt/models/Webcam22`.

Playwright is **not** a repo runtime dependency — it is a dev-only pin in
[`requirements-dev.txt`](../../requirements-dev.txt) (as a comment, because it
is a Node package and this repo has no `package.json`). Install it into a
scratch dir, or next to the checkout for the CI job:

```sh
# scratch dir, for ad-hoc runs
mkdir -p /tmp/pw && cd /tmp/pw && npm init -y >/dev/null
version=$(grep -oE '^# ?playwright==[0-9.]+' "$OLDPWD/requirements-dev.txt" | sed 's/.*playwright==//')
npm install "playwright@$version"
node node_modules/playwright/cli.js install chromium chromium-headless-shell
```

Inside the checkout (what CI does) the same two commands work without a
`package.json`, and `node tools/screenshots/a11y_audit.js` then resolves
`playwright` from `./node_modules`:

```sh
version=$(grep -oE '^# ?playwright==[0-9.]+' requirements-dev.txt | sed 's/.*playwright==//')
npm install --no-save --no-package-lock "playwright@$version"
npx playwright install --with-deps chromium      # ~170MB, cached by CI
```

Either way **a browser download is required**; there is no bundled Chromium and
the script will not fall back to a DOM-only check.

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

## The a11y / visual regression gate (`a11y_audit.js`)

```sh
python3 tools/screenshots/proxy.py &         # stub on :8899
node tools/screenshots/a11y_audit.js         # exits non-zero on any finding
echo $?
```

One navigation **per theme** (Light and Dark by default), six application
states (Timeline, Objects, Motion, the lightbox, Settings, System status) in
each, plus three views re-measured at eight widths and a second context with
`prefers-reduced-motion: reduce`. A full both-theme run takes ~40s. Useful
knobs:

| Var / flag | Default | Meaning |
|------------|---------|---------|
| `A11Y_AUDIT_URL` / `--url` | `http://127.0.0.1:8899/` | Origin to audit (loopback only) |
| `A11Y_AUDIT_FIXTURES` / `--fixtures` | `tools/screenshots/fixtures/gallery` | Fixture root; **refuses to run if it resolves under `/mnt/models`** |
| `A11Y_AUDIT_THEME` / `--theme=light\|dark\|both` | `both` | Which theme(s) every state is audited under |
| `A11Y_AUDIT_JSON` | unset | Also write the full report as JSON (CI uploads it) |
| `A11Y_AUDIT_ALLOW_REMOTE` | unset | Escape hatch for a non-loopback origin |
| `--selfcheck` | – | Non-vacuity proof (below); no proxy needed |

### What it asserts

| Check | Asserts | Threshold |
|-------|---------|-----------|
| `http-clean` | no response ≥ 400, no failed request, no `console.error`, no uncaught exception | – |
| `target-size` | every rendered interactive target | ≥ 24×24 CSS px (WCAG 2.5.8 AA) |
| `text-contrast` | every measured text node against its **composited** background | ≥ 4.5:1 body, ≥ 3:1 large (WCAG 1.4.3 AA) |
| `text-contrast-coverage` | every audited state measures ≥ 80% of its own text nodes | 0.8 floor |
| `h-overflow` | no sideways page scroll at 320/360/390/414/768/1024/1280/1440 | 1px tolerance |
| `reduced-motion` | under `prefers-reduced-motion: reduce`, nothing animates | ≤ 50ms per iteration |
| `image-alt` | every `<img>`/`input[type=image]` has `alt`; no **visible** image is broken (`complete && naturalWidth === 0`) | – |
| `theme-applied` | the page resolved `html[data-theme]` to every requested theme | Light and Dark |

Measurement notes, because these are the parts that are easy to get wrong:

- **Contrast** folds the whole ancestor chain: layers composite outermost
  first, and an `opacity` on any element scales everything painted inside it
  (its own background included). `index.html` draws some labels with an inline
  `opacity: 0.8` on a 10.88px `--text-secondary` over `--bg-tertiary`; the
  static colour pair is 5.7:1, the *painted* pair is 4.21:1, and only the
  composited number is the one a user sees. A text node whose effective
  opacity is below 0.1 is treated as not painted (the hidden toast, a
  crossfaded frame).
- **Gradients are measured, not skipped** (issue #116). A
  `background-image` that is only `linear-`/`radial-`/`conic-gradient` has
  every colour stop extracted — `rgb`/`rgba`/`hsl`/hex, and the usual
  `transparent` tail, which composites to the colour beneath it. Each stop is
  composited over the layers under it and the **worst-case** ratio across the
  plain background colour and all the stops is the one the gate acts on, so
  text is checked against the part of the gradient it is actually worst on.
  Only a real raster `url()` image (a photo) still skips, as
  `background-url-image` — and those nodes are exactly what the coverage gate
  counts.
- **Coverage gate.** Each state's text nodes are counted and the check fails
  when `textChecked === 0` or `textChecked / textNodes < 0.8`. The failure
  message names the per-reason skip histogram (printed in the coverage line and
  in `A11Y_AUDIT_JSON`), and the only waiver is a documented
  `SKIP_EXCEPTIONS` entry: `{ selector, why }`, e.g. a caption painted over a
  full-size url() photo. A contrast check that skipped every node in a state
  used to report PASS over 0 measurements; that is what this gate kills.
- **Themes.** `--theme` (default `both`) seeds `localStorage
  funes-vision.theme` with an init script before the page loads, runs every
  state under Light and Dark, and the run fails if `html[data-theme]` does not
  resolve to the theme that was asked for. Coverage is reported per theme.
- **Target size** skips what is not a target: no layout box (`display:none`, a
  closed `<details>`), `visibility:hidden`, effective opacity 0,
  `pointer-events:none`, `aria-hidden`, and a link in a run of prose (the
  WCAG inline exception). A **full-width** control is exempt on the width
  axis only — a full-width row that is 5px tall still fails. The WCAG
  *spacing* exception is deliberately not implemented: this repo fixed its
  5px chart bars by enlarging the target (#70), not by relying on spacing.
- **Reduced motion** compares the per-iteration duration numerically, so the
  `animation-duration: 0.001ms !important` override the sheet already ships
  passes (1e-06s is not motion) while a 700ms spinner does not. It also fails
  on `scroll-behavior: smooth`. JS-driven motion is out of scope.
- **Overflow** measures from a known scroll origin and names the widest
  unclipped element, so the message says *which* element and whether the
  document scrolls or the surplus is clipped. Content inside a real
  `overflow-x:auto/scroll` box is not page overflow.
- Animations are frozen (finite ones moved to their end state, infinite ones
  left alone) before anything is measured, so a half-faded colour is never
  what gets read. The reduced-motion check runs in its own context *before*
  that, against a real animation list.

Every failure prints the selector, the measured value and the threshold.

### Proving it is not vacuous

```sh
node tools/screenshots/a11y_audit.js --selfcheck
```

Runs the same collectors and the same checkers in a real browser over inline
pages and asserts the outcome: a seeded page (6×6 target, 3.45:1 text pair on a
flat background **and** 3.45:1 text over a `linear-gradient` — the case issue
#116 silently skipped — 2000px block, `<img>` with no `alt`, a broken image, a
refused request, an uncaught exception, a 700ms spin under reduced motion)
**must** fail, a clean page (including readable text over a gradient) **must**
pass, the 0.001ms reduced-motion override **must** pass, and a page whose every
text node is over a `url()` photo **must** trip the coverage gate — unless the
nodes are on the documented exception list. CI runs this before the real audit.

`tests/test_a11y_audit_gate.js` (fast, no browser) pins the same contract from
the other side: the thresholds are the WCAG values, all eight checks are
registered, each check fails on a seeded violation and passes on a compliant
record, the carve-outs still work, the WCAG formula matches the repo's existing
static a11y test, and the CI job cannot lose the pin, the self-check, or its
own ability to fail.

`tests/test_a11y_coverage_gate.js` (fast, no browser) pins the coverage gate
itself: gradient stop extraction (rgb/rgba/hsl/hex/transparent, and a refusal
for colour spaces it cannot read), worst-case compositing across the stops, the
0.8 floor's decision function including the documented waivers, and that the
in-page bundle still ships the helpers that do the measuring.

### Adding an assertion

1. Add a collector in the in-page section (a plain function, no closure over
   module scope) and list it in `inPageBundleSource()`.
2. Add a **pure** `check*(records) -> failures[]` next to the existing ones,
   using the `fail(check, state, selector, detail, fix, data)` helper so the
   message always carries selector + measured value + threshold. Keep the
   policy in the checker, not in the collector, so the test can drive it
   without a browser.
3. Register it in `CHECKS` and map its records in `runAudit`'s check loop.
4. Add it to the assertion list above, and extend
   `tests/test_a11y_audit_gate.js` with a seeded-failure and a
   compliant-pass case. A check that cannot fail a synthetic violation is a
   check that does not exist.
5. Prove it end to end with `--selfcheck` (add a seeded element and an
   expectation), then run the gate against the fixture gallery.


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

Playwright captures are 24-bit RGB PNGs, and the desktop ones are 1440px
wide — far wider than the box the guide paints them into. Before
committing, re-encode them to capped-width 256-colour palette PNGs —
visually unchanged at guide size, and much smaller:

```sh
python3 tools/screenshots/optimize_guide_img.py           # rewrite in place
python3 tools/screenshots/optimize_guide_img.py --check   # read-only gate
```

Both take an optional directory argument to override the default
(`docs/guide/img`).

The script rewrites every `*.png` in place with Pillow's median-cut
quantizer (`optimize=True`) after a Lanczos downscale. Four constants
are the whole policy:

| Constant | Value | What it is |
|----------|-------|------------|
| `MAX_WIDTH` | `1024` | Hard cap on width. Never upscales, so the 780px phone captures pass through at native size |
| `DISPLAY_CSS_WIDTH` | `802` | Measured width of the box an `<img>` in `docs/USER-GUIDE.html` is painted into. `MAX_WIDTH` is 1.28x it, for a 125%-zoomed browser and HiDPI |
| `COLORS` | `256` | Palette size. Cutting it further is what costs quality, not the resample |
| `MAX_PNG_BYTES` / `MAX_TOTAL_BYTES` | `420_000` / `2_600_000` | Per-file and per-directory byte ceilings |

The cap is a mandate, not a target: a re-encode that comes out *larger*
is still written, so "no guide PNG is wider than `MAX_WIDTH`" stays one
unconditional invariant. Two very flat captures (`help.png`, `timeline.png`)
are heavier that way, and that is the trade.

Re-running is a byte-exact no-op — median cut is not a fixed point, so
the tool short-circuits any file already in palette mode at or under the
cap. `--check` re-validates the committed bytes **read-only** (under the
width cap, in palette mode, and inside both byte ceilings) and exits
non-zero on any failure; a write run also exits non-zero if the
directory busts `MAX_TOTAL_BYTES`. Nothing in the build runs `--check`;
`tests/test_guide_img_budget.py` does, and it also proves the gate can
fail (too-wide, too-fat, over-colour, over-ceiling).

Keep the `.png` extensions: `tests/test_screenshot_proxy.py` asserts
`timeline.png` still serves PNG/JPEG bytes. The two GIFs are
`make_gifs.py`'s and are budgeted separately.

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
| `A11Y_AUDIT_URL` | `:8899` | a11y gate origin |

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
