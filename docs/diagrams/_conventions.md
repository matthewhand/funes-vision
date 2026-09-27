# Diagram conventions — funes-vision

Shared rules for every diagram in `docs/diagrams/`. Derived from the `diagram-design`
skill (profile `funes-vision`, dark-only). Read this before editing or adding a diagram.

## Skin (dark, matched to the app UI)

| Role | Hex | Use |
|------|-----|-----|
| `paper` | `#080c14` | page bg, node mask fills, arrow-label masks |
| `paper-2` | `#0f172a` | cards, store fills |
| `ink` | `#f8fafc` | primary text, primary strokes |
| `muted` | `#94a3b8` | secondary text, default arrows |
| `soft` | `#64748b` | sublabels, de-emphasised |
| `rule` | `rgba(248,250,252,0.12)` | hairlines |
| `rule-solid` | `rgba(148,163,184,0.30)` | strong borders |
| `accent` | `#3b82f6` | focal only — **1–2 elements max per diagram** |
| `accent-tint` | `rgba(59,130,246,0.12)` | accent node fill |
| `link` | `#60a5fa` | HTTP/API/external arrows |
| `success` / `warning` / `danger` | `#10b981` / `#f59e0b` / `#ef4444` | state only, never a second focal hue |

Node treatments (fill / stroke):

| Kind | Fill | Stroke |
|------|------|--------|
| Focal (1–2 max) | `rgba(59,130,246,0.12)` | `#3b82f6` 1.2 |
| Service / step | `#0f172a` | `#f8fafc` 1 |
| Store / state | `rgba(248,250,252,0.05)` | `#94a3b8` 1 |
| External | `rgba(248,250,252,0.03)` | `rgba(248,250,252,0.30)` 1 |
| Input / user | `rgba(148,163,184,0.10)` | `#64748b` 1 |
| Optional / async | `rgba(248,250,252,0.02)` | `rgba(248,250,252,0.20)` dashed `4,3` |
| Security boundary | `rgba(59,130,246,0.05)` | `rgba(59,130,246,0.50)` dashed `4,4` |

Zone fill `rgba(248,250,252,0.02)`, stroke `rgba(248,250,252,0.10)`; zone label mask
`#080c14`, label text `rgba(248,250,252,0.40)`. Type tags: `rx=2`, stroke at 0.40,
Geist Mono 7px.

Typography: page title `Instrument Serif`; node names `Geist` 12px/600; technical
sublabels, ports, URLs, arrow labels `Geist Mono` (9px / 8px). Never JetBrains Mono.
Mask fills must always be `#080c14` (opaque over the dark page).

## Non-negotiable connector rules (SKILL.md §6)

1. Right-angle elbows only for off-axis connections (`r=8` quarter arcs). No diagonal `<line>`.
2. Arrow labels need an opaque `#080c14` mask rect and a **6–10px gap** from the stroke.
3. No overlapping/parallel-on-top connectors; crossings use a bridge/hop arc.
4. Multiple connectors on one box edge get their own attach points, ≥12px apart.
5. A connector must not pass behind a non-endpoint box; if unavoidable it is dashed and labelled at the visible end.
6. A label mask must not overlap a node drawn after it.

Draw order = z-order: **background → zones → arrows → nodes/labels**.

## Budgets

Max 9 nodes, 12 arrows, 2 accent elements, 3 zones per diagram. Above that, split into
overview + detail. All coordinates/sizes divisible by 4.

## Required structure

- Header: mono eyebrow (`TYPE · funes-vision`), serif `<h1>`, muted subtitle.
- `<svg>` with `role="img"`, `aria-labelledby="<slug>-title <slug>-desc"`.
- `<title id="<slug>-title">` **first child** of `<svg>`, then `<desc id="<slug>-desc">` (one sentence, content not geometry).
- Legend as a horizontal strip at the bottom, viewBox height extended ~60px.
- Footer: `funes-vision · docs/diagrams` + source `file:line` references.
- Optional summary cards: 2–3 with **varied** widths, `paper-2` background, no shadow.

## Assumptions

Mark anything inferred, not read directly from code, with an inline note in the footer
(e.g. `Assumption: ...`) and list it in `docs/diagrams/README.md`. Prefer the real
component names from the repo (`api_server.py`, `analyze_images.py`, `catalog.py`,
`scans.py`, `taxonomy.py`, `integrations/`, `tools/watchdog.sh`, `systemd/*`).

## Validate

```bash
python3 ~/.claude/skills/diagram-design/scripts/self_check.py docs/diagrams/<file>.html
```

Then re-check: every node/edge against source; labels match implementation terminology;
no clipped text; source JSON/HTML valid.
