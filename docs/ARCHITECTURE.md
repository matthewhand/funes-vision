# Architecture diagram set

How to read the diagrams in [`docs/diagrams/`](diagrams/README.md), and how they
relate to the planned `index.html` decomposition.

This is a **reading guide**, not a second architecture document. For the prose
version and the history of how the system got here, read
[`ARCHITECTURE.md`](../ARCHITECTURE.md) at the repo root. For configuration,
operations and the full contributor reference, read
[`DEVELOP.md`](../DEVELOP.md).

---

## Where to start

| If you are… | Read | Answers |
|---|---|---|
| **new, and want the shape of the thing** | [`01-hero-overview.html`](diagrams/01-hero-overview.html) then [`02-runtime-architecture.html`](diagrams/02-runtime-architecture.html) | What is it, what runs where, what are the trust boundaries? |
| **about to edit `index.html`** | [`14-frontend-internals.html`](diagrams/14-frontend-internals.html) | Which of the 19 lettered sections do I open? |
| **about to change behaviour** | [`15-test-ci-gates.html`](diagrams/15-test-ci-gates.html) | What has to be green before this lands? |
| **adding a file the page loads** | [`16-asset-manifest.html`](diagrams/16-asset-manifest.html) | Which four lists must I add it to? |
| **deploying or debugging a host** | [`03-deployment.html`](diagrams/03-deployment.html) | Ports, volumes, units, cron. |
| **tracing one frame** | [`04-sequence-ingest-analysis.html`](diagrams/04-sequence-ingest-analysis.html) | How does a JPEG become a catalogued detection? |
| **debugging live updates** | [`05-sequence-live-sse.html`](diagrams/05-sequence-live-sse.html) | How do browser, proxy, API and the SSE bus interact? |
| **looking at the data** | [`08-data-flow.html`](diagrams/08-data-flow.html), [`10-data-model.html`](diagrams/10-data-model.html) | Where does frame data go, and what is in the catalogs? |
| **reviewing for security** | [`09-security-boundaries.html`](diagrams/09-security-boundaries.html) | What are the zones, and what is gated? |
| **working on retention or visits** | [`06-sequence-recovery.html`](diagrams/06-sequence-recovery.html), [`11-detection-states.html`](diagrams/11-detection-states.html), [`12-burst-timeline.html`](diagrams/12-burst-timeline.html), [`13-visit-swimlane.html`](diagrams/13-visit-swimlane.html) | What happens when a sweep is stuck, and how does a visit form? |
| **adding an outbound integration** | [`07-integrations.html`](diagrams/07-integrations.html) | What leaves the box, and from which process? |

## How to view them

Each diagram is a **single self-contained HTML file with inline SVG**. There is no
build step, no bundler, no viewer and no network call:

```bash
# any of these work, including with the network unplugged
xdg-open docs/diagrams/14-frontend-internals.html
python3 -m http.server 8000 --directory docs/diagrams   # then browse to :8000
```

The three typefaces (Instrument Serif, Geist, Geist Mono) are **vendored in
[`docs/diagrams/fonts/`](diagrams/fonts/README.md)** and loaded through
[`docs/diagrams/fonts.css`](diagrams/fonts.css) with a relative path, so opening a
file from `file://` on an air-gapped host renders it exactly as designed. Nothing is
fetched from a CDN — `tests/test_no_remote_cdn.js` fails the build if that changes.

**The diagrams are also readable as text.** The SVG is inline, not an image, so the
boxes, labels and connectors are plain markup in the `.html` file. `grep`, `sed` and
a diff all work on them. If you want the node inventory without rendering anything:

```bash
grep -oP '(?<=<text[^>]{0,200}>)[^<]+' docs/diagrams/14-frontend-internals.html
```

**When you change one:** read [`docs/diagrams/_conventions.md`](diagrams/_conventions.md)
first — it holds the shared token table, the connector rules and the budgets. Then
validate:

```bash
python3 ~/.claude/skills/diagram-design/scripts/self_check.py docs/diagrams/<file>.html
bash tests/run.sh
```

## How this relates to the `index.html` decomposition

`index.html` is today one file: a `<style>` block, a body, an external
`lucide.min.js`, and one inline classic `<script>` of roughly 281 KB that holds all
client logic. There is no `package.json` and no build step. The plan is to extract
`js/*.js` ES modules in phases.

Diagram 14 is the map that makes that plan legible. Read it as a claim about where
the seams already are:

- **The `pure:` sentinels are the real module boundaries.** 95 pure helpers are
  already wrapped in a `// === pure:NAME ===` … `// === /pure:NAME ===` pair, and
  a test suite extracts each one and `eval`s it. A pure helper with a passing
  sentinel test is, by construction, a self-contained unit with no dependency on
  `state` or the DOM. Those are the safest first files to extract.
- **The three "concern groups" in the diagram are the natural module names.**
  Routing, state, filtering, rendering, API access, live updates and bootstrap map
  onto the A–S letters as a partition, not as a wish. Diagram 14's grouping is
  derived from the section map at `index.html:2–121`, so it cannot drift from the
  file without someone editing the map.
- **The impure sections are where extraction gets expensive.** The map flags them
  explicitly: the `labelStates` currying helpers, the focus-trap and popover
  machinery, and `computeVisits` all read `state` or the DOM, so they carry no
  sentinel and are covered by text assertions instead. They belong later, in a
  phase that has a way to give them a module.
- **Q, R and S are one function, not three scopes.** They are three slices of a
  single `DOMContentLoaded` arrow function. Splitting the file must not turn them
  into three modules that each own a listener.
- **The sentinel contract has to survive the split.** Once a helper lives in its
  own ES module, the marker pair stops being a *location* and has to become an
  *export*. `tests/helpers/load.cjs`'s `loadSentinel()` is documented as
  transitional for exactly this reason. Whatever replaces it, the property to keep
  is the one in the diagram: **the page and the unit test share one copy of the
  function.**
- **Every extraction adds shipped files.** That is what diagram 16 is about. Each
  new `js/*.js` and `css/*.css` file is a new asset that all four deployment
  targets must enumerate, in the same commit, or it 404s in exactly one of them.

## What the diagrams are not

- **Not generated from the code.** They are hand-drawn and hand-checked against
  source; each footer carries the `file:line` references it was drawn from. If the
  code moves, the footer is how you find out the diagram is stale.
- **Not exhaustive.** Line counts, function names and per-suite detail are lists,
  and belong in the files they describe. Every diagram says what it left out.
- **Not a substitute for the tools.** A diagram can tell you where the SSE bus is;
  it cannot tell you why your frame did not arrive. The incident path is
  `05-sequence-live-sse`, `06-sequence-recovery` and the `tools/watchdog.sh` log.
