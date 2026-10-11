#!/usr/bin/env node
// Headless-browser a11y + visual regression gate for the SPA.
//
// WHY THIS EXISTS: every other check in this repo inspects the DOM as text
// (regex over index.html, `eval` of sentinel-wrapped pure helpers). That is
// blind to the whole class of defect a human sees: a 404 that leaves a blank
// screen, text painted at 3:1, a 5px-wide tap target, a page that scrolls
// sideways on a 320px phone. A headless-browser audit of the fixture gallery
// found ~43 of those while the Python suite, the Node suites, the screenshot
// harness and the a11y unit test were all green.
//
// This script drives Chromium against the SYNTHETIC fixture gallery served by
// tools/screenshots/proxy.py (never a live camera path) and asserts:
//
//   1. http-clean       no response >= 400, no failed request, no console or
//                       page error. This alone catches a missing endpoint --
//                       which is how a blank screen shipped.
//   2. target-size      WCAG 2.5.8 (AA): every rendered interactive target is
//                       at least 24x24 CSS px, with the standard inline /
//                       full-width carve-outs.
//   3. text-contrast    WCAG 1.4.3 (AA): 4.5:1 for body text, 3:1 for large
//                       text, measured against the real composited background
//                       (translucent ancestors and opacity groups folded, not
//                       just the nearest painted colour). The two 1.4.3
//                       exemptions -- an inactive (disabled) control's label
//                       and text layered over a picture -- still come back as
//                       COUNTED skips, so the coverage histogram names them
//                       and the 80% floor keeps counting them.
//   3b. contrast cover. the gate is not vacuous: every audited state measures
//                       enough of its own text. A state where all the text is
//                       skipped -- which is exactly what a gradient or a photo
//                       background used to do -- fails instead of reporting a
//                       clean "PASS" over zero measurements.
//   4. h-overflow       no horizontal page overflow at 320/360/390/414/768/
//                       1024/1280/1440 CSS px.
//   5. reduced-motion   under prefers-reduced-motion: reduce, nothing animates
//                       longer than REDUCED_MOTION_MS -- compared numerically,
//                       so the sheet's 0.001ms "kill motion" override passes
//                       and a real 700ms spinner does not.
//   6. image-alt        every <img> / input[type=image] carries an alt
//                       attribute, and no *visible* image is a broken image
//                       (complete && naturalWidth === 0) -- the blank-screen
//                       symptom a DOM-only test cannot see.
//   7. theme-applied    every state is audited under the requested Light and
//                       Dark themes, and the page really resolved
//                       html[data-theme] to it.
//
// Usage (see tools/screenshots/README.md):
//   python3 tools/screenshots/proxy.py &
//   node tools/screenshots/a11y_audit.js
//   A11Y_AUDIT_URL=http://127.0.0.1:8907/ node tools/screenshots/a11y_audit.js
//   node tools/screenshots/a11y_audit.js --theme=light      # or dark / both
//   node tools/screenshots/a11y_audit.js --selfcheck   # proves it is not vacuous
//
// Playwright is a dev-only dependency, pinned in
// tools/screenshots/browser-deps.json and deliberately absent from
// requirements.txt, which is the runtime set. tests/test_a11y_audit_gate.js
// pins the thresholds and the check registry below, so this gate cannot be
// silently neutered without a test failing.

/* global document, window, getComputedStyle, NodeFilter, CFG */

'use strict';

const path = require('path');
const fs = require('fs');
const http = require('http');

const REPO = path.resolve(__dirname, '..', '..');

// ---------------------------------------------------------------------------
// Thresholds: the WCAG numbers, spelled out. tests/test_a11y_audit_gate.js
// asserts these exact values, so lowering one here fails the fast suite.
// ---------------------------------------------------------------------------
const THRESHOLDS = Object.freeze({
  // WCAG 2.5.8 Target Size (Minimum), Level AA: 24x24 CSS px.
  minTargetPx: 24,
  // WCAG 1.4.3 Contrast (Minimum), Level AA.
  contrastBody: 4.5,
  contrastLarge: 3,
  // 1.4.3 "large scale": >= 18pt (24px), or >= 14pt (18.66px) when bold.
  largeTextPx: 24,
  largeBoldPx: 18.66,
  boldWeight: 700,
  // Coverage gate: the share of a state's text nodes whose contrast must
  // actually be measured. 0.8 means one in five may still be unmeasurable --
  // the lightbox caption over a photo, a canvas label -- but a state where the
  // gate silently measured nothing is the vacuous PASS this threshold exists
  // to kill. Issue #116: a radial-gradient on <body> skipped 84/84 timeline
  // nodes and the gate still said "text-contrast PASS".
  minTextCoverage: 0.8,
  // Themes the page is audited under (index.html resolves 'funes-vision.theme'
  // from localStorage into html[data-theme]).
  themes: Object.freeze(['light', 'dark']),
  // The localStorage key the app reads its appearance preference from, seeded
  // by an init script before the page loads (the app resolves it on
  // DOMContentLoaded, which an evaluate() call would already be too late for).
  themeStorageKey: 'funes-vision.theme',
  // Horizontal overflow: allow 1px of sub-pixel rounding.
  overflowTolerancePx: 1,
  overflowWidths: Object.freeze([320, 360, 390, 414, 768, 1024, 1280, 1440]),
  // Reduced motion: a duration at or below this counts as "no motion", so the
  // 0.001ms !important override in index.html passes and a 700ms spin does
  // not. Compared numerically, never as a string.
  reducedMotionMs: 50,
  // Safety rail: this gate must never point at a live camera directory.
  forbiddenRoots: Object.freeze(['/mnt/models']),
});

// Config handed to the in-page bundle (see inPageBundleSource).
const PAGE_CFG = Object.freeze({
  largeTextPx: THRESHOLDS.largeTextPx,
  largeBoldPx: THRESHOLDS.largeBoldPx,
  boldWeight: THRESHOLDS.boldWeight,
  overflowTolerancePx: THRESHOLDS.overflowTolerancePx,
  // Text painted below this effective opacity (its own opacity times every
  // ancestor's) is not perceivable, so WCAG 1.4.3 does not apply to it. This
  // is the hidden-toast / crossfaded-frame case, not a threshold that lets a
  // dim label through: 10% opacity on a dark surface is invisible.
  invisibleOpacity: 0.1,
});

const DEFAULT_URL = 'http://127.0.0.1:8899/';
const DEFAULT_FIXTURES = path.join(REPO, 'tools', 'screenshots', 'fixtures', 'gallery');

// ---------------------------------------------------------------------------
// Check registry. Every entry is a pure function (records -> failures), so the
// contract test can exercise the policy without launching a browser.
// ---------------------------------------------------------------------------
const CHECKS = [
  {
    id: 'http-clean',
    what: 'no 4xx/5xx response, failed request, console error or page error',
    why: 'a missing endpoint 404s behind the SPA offline fallback and renders a blank screen that looks like success',
    run: checkHttpClean,
  },
  {
    id: 'target-size',
    what: `interactive target >= ${THRESHOLDS.minTargetPx}x${THRESHOLDS.minTargetPx} CSS px (WCAG 2.5.8 AA)`,
    why: 'a 5px chart bar or a 0x0 focusable is unusable with a finger',
    run: checkTargetSize,
  },
  {
    id: 'text-contrast',
    what: `visible text >= ${THRESHOLDS.contrastBody}:1, >= ${THRESHOLDS.contrastLarge}:1 for large text (WCAG 1.4.3 AA)`,
    why: 'low-contrast labels are unreadable in daylight and for low-vision users',
    run: checkTextContrast,
  },
  {
    id: 'text-contrast-coverage',
    what: `each audited state measures >= ${Math.round(THRESHOLDS.minTextCoverage * 100)}% of its own text nodes (${THRESHOLDS.minTextCoverage} floor)`,
    why: 'a contrast check that skipped every node in a state passed while measuring nothing -- that is how issue #116 shipped',
    run: checkTextContrastCoverage,
  },
  {
    id: 'h-overflow',
    what: `no horizontal page overflow at ${THRESHOLDS.overflowWidths.join('/')} CSS px`,
    why: 'a sideways-scrolling page is unusable one-handed on a phone',
    run: checkOverflow,
  },
  {
    id: 'reduced-motion',
    what: `nothing animates longer than ${THRESHOLDS.reducedMotionMs}ms under prefers-reduced-motion: reduce`,
    why: 'vestibular triggers; the OS asked for the motion to stop',
    run: checkReducedMotion,
  },
  {
    id: 'image-alt',
    what: 'every image has an alt attribute; no visible image is broken',
    why: 'a missing alt is a screen-reader dead end; a broken image is a blank screen',
    run: checkImages,
  },
  {
    id: 'theme-applied',
    what: `the page resolves html[data-theme] to every requested theme (${THRESHOLDS.themes.join('/')})`,
    why: 'an audit that measures the wrong theme reports colours the user will never see',
    run: checkThemeApplied,
  },
];

// ---------------------------------------------------------------------------
// Failure helpers
// ---------------------------------------------------------------------------
function fail(check, state, selector, detail, fix, data) {
  return { check, state, selector, detail, fix, data };
}

function fmt2(n) {
  return typeof n === 'number' && isFinite(n) ? Math.round(n * 100) / 100 : n;
}

function truncate(s) {
  const t = String(s).replace(/\s+/g, ' ').trim();
  return t.length > 40 ? t.slice(0, 40) + '…' : t;
}

// ---------------------------------------------------------------------------
// 1. HTTP / console / page errors
// ---------------------------------------------------------------------------
function checkHttpClean(records) {
  const out = [];
  for (const r of records) {
    if (r.status >= 400) {
      out.push(fail('http-clean', r.state, r.url,
        `HTTP ${r.status}`,
        'this route must answer 2xx -- a 404 here is exactly what renders a blank screen'));
    } else if (r.failed) {
      out.push(fail('http-clean', r.state, r.url,
        `request failed (${r.failure})`,
        'the request never completed; check the stub route and the URL the SPA built'));
    } else if (r.pageError) {
      out.push(fail('http-clean', r.state, 'pageerror (uncaught exception)',
        `uncaught: ${r.pageError}`,
        'fix it or make it non-fatal -- no existing check in this repo can see it'));
    } else if (r.console) {
      out.push(fail('http-clean', r.state, r.consoleSource || 'console',
        `console error: ${truncate(r.console)}`,
        'the page logs an error on load; make it non-fatal or fix the cause'));
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// 2. WCAG 2.5.8 target size
// ---------------------------------------------------------------------------
function checkTargetSize(records) {
  const min = THRESHOLDS.minTargetPx;
  const out = [];
  for (const r of records) {
    // Not a target: never laid out (display:none, closed <details>), hidden
    // from the user, hidden from assistive tech, or not clickable at all.
    if (!r.rendered || r.visibility === 'hidden' || r.opacity === 0) continue;
    if (r.pointerEvents === 'none' || r.ariaHidden) continue;
    // WCAG 2.5.8 inline exception: a link inside a run of prose.
    if (r.inlineInText) continue;
    // The full-width carve-out covers the width axis only: a full-width row
    // that is 5px tall is still not a usable target.
    const needW = r.fullWidth || r.w >= min;
    const needH = r.h >= min;
    if (needW && needH) continue;
    const need = needW ? `height >= ${min}px` : `${min}x${min}px`;
    out.push(fail('target-size', r.state, r.selector,
      `measured ${fmt2(r.w)}x${fmt2(r.h)}px, required ${need} (WCAG 2.5.8 AA)`
        + (r.fullWidth ? ' [full-width element: width axis exempt]' : ''),
      r.name
        ? `"${truncate(r.name)}" needs a min-width/min-height floor -- widen the hit area, not the paint`
        : 'needs a min-width/min-height floor (or an ::after hit area) to reach 24x24',
      { tag: r.tag, role: r.role, name: r.name, w: r.w, h: r.h, required: need }));
  }
  return out;
}

// ---------------------------------------------------------------------------
// 3. WCAG 1.4.3 text contrast, against the composited background
// ---------------------------------------------------------------------------
// sRGB relative luminance per WCAG 2.x.
function relativeLuminance(rgb) {
  const lin = rgb.map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
}

function contrastRatio(fg, bg) {
  const l1 = relativeLuminance(fg);
  const l2 = relativeLuminance(bg);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

function checkTextContrast(records) {
  const out = [];
  for (const r of records) {
    if (r.skip) continue; // counted in the coverage line, never silently dropped
    const required = r.large ? THRESHOLDS.contrastLarge : THRESHOLDS.contrastBody;
    // The ratio is normally stamped on by withRatios(); recompute it here so
    // the check is self-contained for callers that only have the colours.
    // For a gradient record that is the WORST-CASE ratio across its stops.
    const worst = typeof r.ratio === 'number' ? { ratio: r.ratio } : worstCasePair(r);
    if (!worst) continue;
    const ratio = worst.ratio;
    // 0.05 of slack so a 4.499:1 rounding artefact does not fail the gate.
    if (ratio >= required - 0.05) continue;
    out.push(fail('text-contrast', r.state, r.selector,
      `measured ${ratio.toFixed(2)}:1 (${r.fg} on ${r.bg}), required ${required}:1 `
        + `(${r.large ? 'large' : 'body'} text at ${r.fontSize}px/${r.fontWeight}) (WCAG 1.4.3 AA)`,
      r.text
        ? `"${truncate(r.text)}" needs more contrast -- the sheet already ships a readable-on-tinted token for this kind of surface`
        : 'this text needs more contrast against its background',
      { text: r.text, ratio: Math.round(ratio * 100) / 100, required, fg: r.fg, bg: r.bg }));
  }
  return out;
}

// ---------------------------------------------------------------------------
// 3b. Contrast coverage: the check that stops a contrast check from passing
//     vacuously. Issue #116 shipped because <body> gained a radial-gradient and
//     every text node in every state came back as a skip, so text-contrast had
//     nothing to fail on and reported PASS over 0 of 84 measured nodes.
//
// Pure: text records -> failures, one finding per state that measured too
// little. The skip-reason histogram rides in the message, so a failing run
// says *why* the nodes were not measured -- that is the whole point.
// ---------------------------------------------------------------------------
// Documented waivers: a skip on this list is not counted against the state's
// coverage, because the contrast genuinely is not measurable in CSS. Every
// entry names a selector and says why; the format is the contract.
//
//   { selector: '#lightbox-footer', why: 'painted over the url() photo ...' }
//
// Deliberately empty today: the app paints no text over a url() background, so
// every skip it produces still has to be earned back by measuring more nodes.
const SKIP_EXCEPTIONS = Object.freeze([]);
// Only url() photos qualify. An unparseable colour is a bug to fix, never a
// waiver: those nodes stay measured against the floor.
const WAIVED_SKIP_REASONS = Object.freeze(['background-url-image']);
// WCAG 1.4.3 exempts the label of an inactive control outright, so those nodes
// are exempt rather than unmeasurable: no stylesheet could make them readable.
// They stay in the skip histogram and are printed next to the coverage figure,
// but they come out of the denominator.
const EXEMPT_SKIP_REASONS = Object.freeze(['disabled-control']);
const SKIP_HISTOGRAM_CAP = 8;

function pct(n) {
  return Math.round(n * 1000) / 10;
}

function exemptSkipCount(histogram) {
  let n = 0;
  for (const reason of EXEMPT_SKIP_REASONS) n += histogram[reason] || 0;
  return n;
}

function isSkipWaived(rec, exceptions) {
  if (!rec.skip) return false;
  if (WAIVED_SKIP_REASONS.indexOf(rec.skip) === -1) return false;
  const sel = String(rec.selector || '');
  return (exceptions || []).some((ex) => ex.selector && sel.indexOf(ex.selector) !== -1);
}

function summariseCoverage(stateRecords, exceptions) {
  const total = stateRecords.length;
  const checked = stateRecords.filter((r) => !r.skip).length;
  const waived = stateRecords.filter((r) => isSkipWaived(r, exceptions)).length;
  const histogram = {};
  for (const r of stateRecords) {
    if (r.skip) histogram[r.skip] = (histogram[r.skip] || 0) + 1;
  }
  const exempt = exemptSkipCount(histogram);
  const denom = total - exempt;
  return {
    textNodes: total,
    textChecked: checked,
    textSkipped: total - checked,
    textWaived: waived,
    textExempt: exempt,
    skipReasons: histogram,
    coverage: denom ? (checked + waived) / denom : 0,
  };
}

function skipHistogramText(histogram) {
  const entries = Object.keys(histogram)
    .sort((a, b) => histogram[b] - histogram[a] || (a < b ? -1 : 1))
    .map((r) => r + ':' + histogram[r]);
  const shown = entries.slice(0, SKIP_HISTOGRAM_CAP);
  const more = entries.length - shown.length;
  return (more > 0 ? shown.join(', ') + ', …+' + more + ' more' : shown.join(', ')) || 'none';
}

function checkTextContrastCoverage(records, exceptions) {
  const min = THRESHOLDS.minTextCoverage;
  const out = [];
  const byState = new Map();
  for (const r of records) {
    const key = r.state || '(no state)';
    if (!byState.has(key)) byState.set(key, []);
    byState.get(key).push(r);
  }
  for (const [state, recs] of byState) {
    const c = summariseCoverage(recs, exceptions);
    const measured = c.textChecked + c.textWaived;
    // WCAG 1.4.3 exempts a disabled control's label outright, so those nodes
    // leave the denominator: they are exempt, not unmeasured. The count still
    // rides along in skipReasons and in the exempt-disabled figure below.
    const measurable = c.textNodes - c.textExempt;
    const exemptNote = ', exempt-disabled: ' + c.textExempt;
    if (measured === 0) {
      out.push(fail('text-contrast-coverage', state, 'state',
        `measured ${measured} of ${c.textNodes} text node(s) -- the contrast assertion is vacuous in this state`
        + exemptNote
        + ` (skip reasons: ${skipHistogramText(c.skipReasons)})`,
        'a gradient-only background-image IS measured (worst case across its stops); only a url() photo'
        + ' or an unparseable colour skips, and those nodes must stay a small minority'
        + ` (floor: ${Math.round(min * 100)}% of the state's text)`));
      continue;
    }
    if (c.coverage < min) {
      out.push(fail('text-contrast-coverage', state, 'state',
        `measured ${measured} of ${measurable} text node(s) = ${pct(c.coverage)}% coverage,`
        + exemptNote
        + ` below the ${pct(min)}% floor (skip reasons: ${skipHistogramText(c.skipReasons)})`,
        'raise the share of nodes actually measured: replace url() photo backgrounds with colours,'
        + ' or add a documented { selector, why } entry to SKIP_EXCEPTIONS'));
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// 7. Theme: the audit must run every state under both Light and Dark, so the
//    page has to have really resolved the theme it was asked for.
// ---------------------------------------------------------------------------
function checkThemeApplied(records) {
  const out = [];
  for (const r of records) {
    if (r.applied === r.theme) continue;
    out.push(fail('theme-applied', r.state, 'html[data-theme]',
      `asked for theme "${r.theme}" but the page resolved "${r.applied || 'unset'}"`,
      'the init script must seed localStorage before load and the app must honour it -- auditing the wrong theme measures colours the user never sees'));
  }
  return out;
}

// ---------------------------------------------------------------------------
// 4. Horizontal page overflow
// ---------------------------------------------------------------------------
function checkOverflow(records) {
  const out = [];
  const tol = THRESHOLDS.overflowTolerancePx;
  for (const r of records) {
    const over = r.scrollWidth - r.clientWidth;
    if (over <= tol) continue;
    const named = (r.offenders || []).slice(0, 3)
      .map((o) => `${o.selector} (${o.w}px wide at x=${o.x})`);
    // overflow-x:hidden can stop the document scrolling without stopping the
    // content running past the edge -- say which of the two actually happens.
    const how = r.scrollX > 0
      ? `the page scrolls sideways by ${r.scrollX}px`
      : 'the surplus is clipped by an overflow-x:hidden ancestor, so that content is cut off';
    out.push(fail('h-overflow', `${r.state} @ ${r.width}px`,
      named.length ? named.join(' | ') : 'html, body (no unclipped child identified)',
      `scrollWidth ${r.scrollWidth} > clientWidth ${r.clientWidth}: ${how} (tolerance ${tol}px)`,
      named.length
        ? 'constrain these to the viewport: max-width:100%, min-width:0 on the flex/grid child, or let the container scroll'
        : 'the document scrolls sideways; constrain the widest child to the viewport',
      { width: r.width, over, scrollX: r.scrollX, offenders: r.offenders }));
  }
  return out;
}

// ---------------------------------------------------------------------------
// 5. prefers-reduced-motion
// ---------------------------------------------------------------------------
function checkReducedMotion(records) {
  const max = THRESHOLDS.reducedMotionMs;
  const out = [];
  for (const r of records) {
    if (r.scrollBehavior && r.scrollBehavior !== 'auto') {
      out.push(fail('reduced-motion', r.state, r.scrollSelector || 'html',
        `scroll-behavior: ${r.scrollBehavior} under prefers-reduced-motion: reduce`,
        'set scroll-behavior: auto !important inside the reduced-motion media query'));
    }
    for (const a of r.animations || []) {
      if (!a.visible) continue; // nothing is seen animating inside a hidden subtree
      // Numeric comparison on the per-iteration duration: a 0.001ms override is
      // "no motion", a 700ms spin is motion, and neither depends on how the
      // duration is spelled.
      if (!(a.durationMs > max)) continue;
      const iters = a.iterations === Infinity ? 'infinite' : fmt2(a.iterations);
      out.push(fail('reduced-motion', r.state, a.selector,
        `${a.type} "${a.name}" runs ${a.durationMs}ms x ${iters}, required <= ${max}ms`,
        'neutralise it in @media (prefers-reduced-motion: reduce) -- the sheet already does this for * / ::before / ::after, so a specific rule is winning over it',
        a));
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// 6. Images: alt attribute + no broken visible image
// ---------------------------------------------------------------------------
// An empty alt="" is a valid decorative image, so it is never reported as a
// broken image; the blank-screen class the gate targets is a *content* image
// that should paint and does not.
function imageBroken(r) {
  if (!r.shown || !r.complete || r.naturalWidth !== 0) return false;
  return !!(r.alt || '').trim();
}

function checkImages(records) {
  const out = [];
  for (const r of records) {
    if (!r.hasAlt) {
      out.push(fail('image-alt', r.state, r.selector,
        `no alt attribute (src ${r.src})`,
        'add alt="" for a decorative image, or a short description of the content',
        r));
    }
    if (imageBroken(r)) {
      out.push(fail('image-alt', r.state, r.selector,
        `broken image: rendered with alt="${truncate(r.alt || '')}" but naturalWidth 0 after load (src ${r.src})`,
        'this is the blank-screen class: a card renders an empty frame. Fix the src/data-src, or the route that should serve it',
        r));
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// In-page collectors.
//
// These run inside the browser, so they are shipped as one init script (see
// inPageBundleSource) rather than through page.evaluate: evaluate()
// serialises a single function and drops its free variables. Every number
// they need comes from CFG, which is generated from THRESHOLDS, and
// tests/test_a11y_audit_gate.js asserts the two agree at runtime.
// ---------------------------------------------------------------------------

function inPageCssPath(el) {
  if (!el || el.nodeType !== 1) return String(el);
  const parts = [];
  let node = el;
  let depth = 0;
  while (node && node.nodeType === 1 && depth < 5) {
    let part = node.tagName.toLowerCase();
    if (node.id) {
      parts.unshift(part + '#' + node.id);
      break;
    }
    const cls = (node.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
    if (cls.length) part += '.' + cls.join('.');
    const parent = node.parentElement;
    if (parent) {
      const sibs = Array.prototype.filter.call(parent.children, (c) => c.tagName === node.tagName);
      if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(node) + 1) + ')';
    }
    parts.unshift(part);
    node = node.parentElement;
    depth += 1;
  }
  const s = parts.join(' > ');
  return s.length > 140 ? s.slice(0, 140) + '…' : s;
}

function inPageParseColor(str) {
  if (!str) return null;
  const s = str.trim();
  if (s === 'transparent') return { r: 255, g: 255, b: 255, a: 0 };
  let m = s.match(/^rgba?\(([^)]+)\)$/i);
  if (m) {
    const p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
    if (p.length < 3 || !p.slice(0, 3).every((n) => isFinite(n))) return null;
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  }
  m = s.match(/^color\(srgb\s+([^)]+)\)$/i);
  if (m) {
    const p = m[1].split(/[\s/]+/).filter(Boolean).map(Number);
    if (p.length < 3) return null;
    return { r: p[0] * 255, g: p[1] * 255, b: p[2] * 255, a: p.length > 3 ? p[3] : 1 };
  }
  const hex = s.match(/^#([0-9a-f]{3,8})$/i);
  if (hex) {
    const h = hex[1];
    const full = h.length <= 4 ? h.split('').map((x) => x + x).join('') : h;
    return {
      r: parseInt(full.slice(0, 2), 16),
      g: parseInt(full.slice(2, 4), 16),
      b: parseInt(full.slice(4, 6), 16),
      a: h.length === 8 ? parseInt(full.slice(6, 8), 16) / 255 : 1,
    };
  }
  m = s.match(/^hsla?\(([^)]+)\)$/i);
  if (m) {
    const p = m[1].split(/[,\s/]+/).filter(Boolean);
    const h = parseFloat(p[0]);
    const sS = parseFloat(p[1]) / 100;
    const l = parseFloat(p[2]) / 100;
    if (![h, sS, l].every((n) => isFinite(n))) return null;
    const c = (1 - Math.abs(2 * l - 1)) * sS;
    const hp = ((h % 360) + 360) % 360 / 60;
    const x = c * (1 - Math.abs((hp % 2) - 1));
    const rgb = hp < 1 ? [c, x, 0] : hp < 2 ? [x, c, 0] : hp < 3 ? [0, c, x]
      : hp < 4 ? [0, x, c] : hp < 5 ? [x, 0, c] : [c, 0, x];
    const m0 = l - c / 2;
    return {
      r: (rgb[0] + m0) * 255, g: (rgb[1] + m0) * 255, b: (rgb[2] + m0) * 255,
      a: p.length > 3 ? parseFloat(p[3]) : 1,
    };
  }
  return null;
}

// Colour stops inside a gradient value. `transparent` is a stop too (a=0) --
// a gradient almost always tails off into it, and compositing it is what hands
// the underlying colour back.
function inPageGradientStops(value) {
  const re = /rgba?\([^)]*\)|hsla?\([^)]*\)|#[0-9a-f]{3,8}\b|transparent\b/gi;
  const stops = [];
  let m;
  while ((m = re.exec(value))) {
    const c = inPageParseColor(m[0]);
    if (c) stops.push(c);
    else return { unparsed: inPageTruncate(m[0]) };
  }
  // Any other function in the value is a colour this gate cannot read
  // (oklab(), color-mix(), lab(), var()...). Reporting it keeps the skip
  // honest: measuring a known subset of the stops could pass text that is
  // actually painted in the colour we failed to parse.
  const fns = value.match(/-?[a-zA-Z][a-zA-Z0-9-]*\(/g) || [];
  for (const f of fns) {
    const name = f.slice(0, -1).toLowerCase().replace(/^(-moz-|-ms-|-o-|-webkit-)/, '');
    if (!/^(repeating-)?(linear|radial|conic)-gradient$|^rgba?$|^hsla?$/.test(name)) {
      return { unparsed: inPageTruncate(name + '(...)') };
    }
  }
  return { stops };
}

// One computed background-image is a comma-separated LAYER LIST whose first
// entry paints on top, and the commas inside a gradient's own arguments must
// not split it. Returns the layers bottom-first (paint order). A layer whose
// value is `none` paints nothing, so it is dropped: Chrome lists it alongside
// the layers that DO paint (`radial-gradient(...), none`), and classifying it
// would skip every element whose chain holds that element.
function inPageBgImageLayers(value) {
  const raw = String(value).trim();
  if (!raw || raw === 'none') return [];
  const parts = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < raw.length; i += 1) {
    const ch = raw[i];
    if (ch === '(') depth += 1;
    else if (ch === ')') depth = Math.max(0, depth - 1);
    else if (ch === ',' && depth === 0) {
      parts.push(raw.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(raw.slice(start));
  const layers = parts.map((p) => p.trim()).filter((l) => l && l !== 'none');
  return layers.reverse();
}

// Classify one background layer: a real raster image (url()) is not measurable
// in CSS and must stay a skip; a gradient is, by measuring its worst stop;
// anything else cannot be classified and must stay a skip.
function inPageBgLayer(layer) {
  if (/url\(/i.test(layer)) return { url: true };
  if (/-gradient\(/i.test(layer)) return { gradient: inPageGradientStops(layer) };
  return { unparsed: true };
}

// Paint order for one element's background: the colour paints beneath its
// image layers, which paint bottom-first on top of it.
function inPagePaintLayers(backgroundImage, backgroundColor, opacity, paint) {
  const bg = inPageParseColor(backgroundColor);
  if (!bg) return 'unparsed-background';
  if (bg.a > 0) paint.push({ kind: 'color', color: bg, opacity });
  // inPageBgImageLayers() already drops `none` (and the empty string), so the
  // layer list is the only thing that decides whether anything paints here.
  for (const l of inPageBgImageLayers(backgroundImage)) {
    const cls = inPageBgLayer(l);
    if (cls.url) return 'background-url-image';
    if (cls.gradient && cls.gradient.unparsed) return 'unparsed-gradient';
    if (cls.gradient && cls.gradient.stops.length) {
      paint.push({ kind: 'gradient', stops: cls.gradient.stops, opacity });
    } else if (cls.unparsed) {
      return 'unparsed-background';
    } else if (cls.gradient) {
      return 'unparsed-gradient';
    }
  }
  return null;
}

// Composite one paint layer over every candidate underneath it. A gradient
// branches: each of its stops lands on every existing candidate, so one
// translucent gradient over one translucent colour yields several stocks. An
// opaque layer collapses the set back to its own colour, which is what keeps a
// deep ancestor chain from exploding.
function inPageCompositeLayer(cands, layer) {
  const out = [];
  for (const base of cands) {
    const stops = layer.kind === 'gradient' ? layer.stops : [layer.color];
    for (const c of stops) {
      out.push(inPageOver({ r: c.r, g: c.g, b: c.b, a: c.a * layer.opacity }, base));
    }
  }
  return inPageDedupeColors(out);
}

function inPageDedupeColors(list) {
  const out = [];
  for (const c of list) {
    const near = out.some((o) => Math.round(o.r) === Math.round(c.r)
      && Math.round(o.g) === Math.round(c.g)
      && Math.round(o.b) === Math.round(c.b));
    if (!near) out.push(c);
  }
  // Bound the work: an opaque layer collapses the set anyway, so 24 is far more
  // than a real stylesheet produces (the app's worst chain is <body>'s
  // gradient, which yields two).
  return out.slice(0, 24);
}

// Worst case across the plain background colour and every gradient stop: one
// entry per candidate, so Node stamps the ratio with the single WCAG
// implementation and the worst one wins.
function inPageBgVariants(fg, cum, candidates) {
  return candidates.map((bg) => {
    const fgEff = inPageOver({ r: fg.r, g: fg.g, b: fg.b, a: fg.a * cum }, bg);
    return {
      fg: inPageHex(fgEff),
      bg: inPageHex(bg),
      fgRgb: [Math.round(fgEff.r), Math.round(fgEff.g), Math.round(fgEff.b)],
      bgRgb: [Math.round(bg.r), Math.round(bg.g), Math.round(bg.b)],
    };
  });
}

function inPageHex(c) {
  const h = (v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0');
  return '#' + h(c.r) + h(c.g) + h(c.b);
}

function inPageOver(fg, bg) {
  const a = Math.max(0, Math.min(1, fg.a));
  return { r: fg.r * a + bg.r * (1 - a), g: fg.g * a + bg.g * (1 - a), b: fg.b * a + bg.b * (1 - a), a: 1 };
}

function inPageEffectiveAlpha(el) {
  let o = 1;
  for (let e = el; e; e = e.parentElement) {
    const s = getComputedStyle(e);
    o *= Number(s.opacity);
    if (e.tagName === 'HTML') break;
  }
  return o;
}

// WCAG 2.5.8 inline exception: a link sitting in a run of prose. Only real
// links qualify, and only when the parent is a normal block -- in a flex or
// grid row a "sibling text" node is a label, not a sentence.
function inPageInlineInText(el, cs) {
  if (el.tagName !== 'A') return false;
  if (cs.display.indexOf('inline') !== 0) return false;
  const p = el.parentElement;
  if (!p) return false;
  const pd = getComputedStyle(p);
  if (pd.display.indexOf('flex') !== -1 || pd.display.indexOf('grid') !== -1) return false;
  let sib = '';
  for (const n of p.childNodes) {
    if (n !== el && n.nodeType === 3) sib += n.nodeValue;
  }
  return sib.trim().length > 0;
}

function inPageTruncate(s) {
  const t = String(s).replace(/\s+/g, ' ').trim();
  return t.length > 40 ? t.slice(0, 40) + '…' : t;
}

// WCAG 1.4.3 exempts inactive UI components, so text inside a disabled control
// is not required to meet a contrast ratio. The exemption is wider than the
// `disabled` attribute: a <fieldset disabled> cascades to everything inside it
// (which closest() already returns), and an author can say the same thing with
// aria-disabled on any ancestor.
function inPageInsideDisabledControl(el) {
  const ctl = el.closest('button,input,select,textarea,fieldset');
  if (ctl && ctl.disabled === true) return true;
  for (let e = el; e; e = e.parentElement) {
    if (e.getAttribute('aria-disabled') === 'true') return true;
    if (e.tagName === 'HTML') break;
  }
  return false;
}

// Is this picture painted right now? A display:none or fully cross-faded
// ancestor leaves no boxes at all, and the same reasoning as the `shown`
// computation in inPageCollectImages() applies: it must not be mistaken for
// something text is layered over.
function inPageMediaShown(m) {
  if (!m.getClientRects().length) return false;
  for (let e = m; e; e = e.parentElement) {
    const s = getComputedStyle(e);
    if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
    if (e.tagName === 'HTML') break;
  }
  return true;
}

// Text layered over a picture -- an overlay caption on a photo, a badge on a
// video poster. WCAG 1.4.3 exempts text that is part of an image, and a CSS
// ratio against the pixels underneath it is meaningless. Deliberately
// conservative: every condition has to hold, so text that merely sits beside
// or under an <img> is still measured.
//   * the picture is really painted and the TEXT's centre falls inside it;
//   * the picture is not the text's own box -- an <img> inside a paragraph is
//     layout, not an overlay;
//   * the text is layered over it: some element between the text and the branch
//     point it shares with the picture is position:absolute/fixed.
function inPageOverMedia(media, el, r) {
  const cx = r.left + r.width / 2;
  const cy = r.top + r.height / 2;
  for (const m of media) {
    if (el.contains(m) || m.contains(el)) continue;
    if (!inPageMediaShown(m)) continue;
    const mr = m.getBoundingClientRect();
    if (mr.width <= 0 || mr.height <= 0) continue;
    if (cx < mr.left || cx > mr.right || cy < mr.top || cy > mr.bottom) continue;
    // The overlay stack is everything on the TEXT's side of the branch point
    // with the picture. Walking up from the text element, the first element
    // that also contains the picture is that branch point (the lowest common
    // ancestor): stop there, and do not test it -- a positioned wrapper that
    // holds BOTH the image and the text is layout, not an overlay.
    //
    // This is the real app's caption: .card-overlay{position:absolute} >
    // .meta-info > span, with the thumbnail <img> a SIBLING of .card-overlay
    // under .image-card{position:relative}. Requiring the positioned ancestor
    // itself to contain the image never matched that shape, so no caption was
    // ever exempted. Any positioned layer anywhere below the branch point --
    // including the text element itself -- means the text is painted on top.
    for (let e = el; e && e.tagName !== 'HTML'; e = e.parentElement) {
      if (e.contains(m)) break;
      const pos = getComputedStyle(e).position;
      if (pos === 'absolute' || pos === 'fixed') return true;
    }
  }
  return false;
}

function inPageCollectTargets() {
  const SEL = [
    'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary',
    'label[for]', '[role=button]', '[role=tab]', '[role=switch]', '[role=checkbox]',
    '[role=menuitem]', '[role=option]', '[tabindex]:not([tabindex="-1"])', '[onclick]',
  ].join(', ');
  const vw = document.documentElement.clientWidth;
  const out = [];
  for (const el of document.querySelectorAll(SEL)) {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    out.push({
      selector: inPageCssPath(el),
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      name: inPageTruncate((el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || '')),
      w: r.width,
      h: r.height,
      x: r.left,
      y: r.top,
      rendered: el.getClientRects().length > 0,
      display: cs.display,
      visibility: cs.visibility,
      pointerEvents: cs.pointerEvents,
      opacity: inPageEffectiveAlpha(el),
      ariaHidden: !!el.closest('[aria-hidden="true"], [hidden]'),
      inlineInText: inPageInlineInText(el, cs),
      fullWidth: r.width >= vw - 1,
    });
  }
  return out;
}

function inPageCollectText() {
  const SKIP_TAGS = { SCRIPT: 1, STYLE: 1, TITLE: 1, NOSCRIPT: 1, TEXTAREA: 1, OPTION: 1, HEAD: 1 };
  const out = [];
  // The pictures the overlay exemption tests against, built once: the walk below
  // runs for every text node in the page.
  const media = document.querySelectorAll('img,video,canvas');
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
  let node = walker.nextNode();
  while (node) {
    const raw = node.nodeValue;
    const el = node.parentElement;
    node = walker.nextNode();
    if (!raw || !raw.trim()) continue;
    if (!el || SKIP_TAGS[el.tagName]) continue;
    if (!el.getClientRects().length) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || Number(cs.opacity) === 0) continue;
    const fontSize = parseFloat(cs.fontSize) || 0;
    if (fontSize === 0) continue;
    // Screen-reader-only patterns (1x1 clip, off-screen negative indent).
    if (r.width <= 1 && r.height <= 1) continue;
    if (parseFloat(cs.textIndent) <= -100) continue;
    const text = raw.trim().replace(/\s+/g, ' ').slice(0, 60);
    const ref = { selector: inPageCssPath(el), text };

    // Two WCAG 1.4.3 exemptions, decided before anything is measured: a label
    // inside an inactive (disabled) control, and text layered over a picture.
    // Both come back as a COUNTED skip -- the same record shape as the
    // unmeasurable-background ones -- so summarise()'s skip-reason histogram
    // names them and the coverage floor still counts them.
    if (inPageInsideDisabledControl(el)) {
      out.push(Object.assign({ skip: 'disabled-control' }, ref));
      continue;
    }
    if (media.length && inPageOverMedia(media, el, r)) {
      out.push(Object.assign({ skip: 'over-image' }, ref));
      continue;
    }

    // Fold the ancestor chain. Paint order is outermost first; an `opacity`
    // on any element scales everything painted inside it, including its own
    // background, so a layer's effective alpha is its own alpha times the
    // cumulative opacity of itself and all its descendants up to the text.
    // A raster url() image or an unparseable colour is reported as a skip so
    // the coverage line shows it instead of it disappearing. A gradient
    // background is MEASURED: every colour stop in it becomes a candidate
    // behind the text and the worst case is what gets audited.
    const chain = [];
    for (let e = el; e; e = e.parentElement) {
      chain.unshift(e);
      if (e.tagName === 'HTML') break;
    }
    let skip = null;
    let cum = 1;
    const paint = [];
    for (const e of chain) {
      const s = getComputedStyle(e);
      cum *= Number(s.opacity);
      // Layer list order: background colour beneath its image layers.
      skip = skip || inPagePaintLayers(s.backgroundImage, s.backgroundColor, cum, paint);
    }
    // An ancestor's opacity can hide the text without hiding its own box, so
    // the visibility test has to use the cumulative value, not this element's.
    if (cum < CFG.invisibleOpacity) continue;

    const fg = inPageParseColor(cs.color);
    if (!fg) {
      out.push(Object.assign({ skip: 'unparsed-color', value: String(cs.color).slice(0, 40) }, ref));
      continue;
    }
    if (fg.a === 0) continue; // nothing is painted
    if (skip) {
      out.push(Object.assign({ skip }, ref));
      continue;
    }
    // Canvas fallback: the HTML spec paints the canvas white when the root
    // background is transparent.
    let candidates = [{ r: 255, g: 255, b: 255, a: 1 }];
    for (const layer of paint) {
      candidates = inPageCompositeLayer(candidates, layer);
    }
    const weight = parseInt(cs.fontWeight, 10) || 400;
    const large = fontSize >= CFG.largeTextPx
      || (fontSize >= CFG.largeBoldPx && weight >= CFG.boldWeight);
    out.push({
      selector: ref.selector,
      text,
      fontSize,
      fontWeight: weight,
      large,
      // One entry per composited candidate (plain colour + every gradient
      // stop). withRatios() stamps the worst ratio onto the record, so the
      // finding text, the coverage line and the JSON report all quote it.
      bgVariants: inPageBgVariants(fg, cum, candidates),
      // First candidate, for eyeballing the report; bgVariants carries the rest.
      bg: inPageHex(candidates[0]),
    });
  }
  return out;
}

function inPageCollectOverflow() {
  const de = document.documentElement;
  // Measure from a known scroll origin: getBoundingClientRect is viewport
  // relative, so a page left scrolled sideways would hide its own offender.
  window.scrollTo(0, window.scrollY);
  const vw = de.clientWidth;
  const tol = CFG.overflowTolerancePx;
  const offenders = [];
  for (const el of document.querySelectorAll('body, body *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    if (!el.getClientRects().length) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    if (r.right <= vw + tol && r.left >= -tol) continue;
    // Content inside a real scroller (overflow-x: auto/scroll) is not page
    // overflow -- the user scrolls that box, not the document. body/html are
    // never treated as such a box: an element running past their edge is what
    // makes the *page* scroll, which is exactly what this asserts.
    let clipped = false;
    for (let p = el.parentElement; p && p !== de; p = p.parentElement) {
      if (p === document.body) break;
      if (getComputedStyle(p).overflowX !== 'visible') {
        clipped = true;
        break;
      }
    }
    if (clipped) continue;
    offenders.push({
      selector: inPageCssPath(el),
      x: Math.round(r.left),
      right: Math.round(r.right),
      w: Math.round(r.width),
    });
  }
  offenders.sort((a, b) => (b.right - vw) - (a.right - vw));
  // Does the document actually scroll sideways, or is the surplus clipped?
  window.scrollTo(99999, window.scrollY);
  const scrollX = window.scrollX;
  window.scrollTo(0, window.scrollY);  return {
    width: vw,
    scrollWidth: Math.max(de.scrollWidth, document.body ? document.body.scrollWidth : 0),
    clientWidth: vw,
    scrollX,
    offenders: offenders.slice(0, 6),
    offenderCount: offenders.length,
  };
}

function inPageCollectMotion() {
  const animations = [];
  for (const a of document.getAnimations()) {
    if (!a.effect || !a.effect.getComputedTiming) continue;
    const timing = a.effect.getComputedTiming();
    const target = a.effect.target;
    const isEl = !!(target && target.nodeType === 1);
    const cs = isEl ? getComputedStyle(target) : null;
    animations.push({
      type: (a.constructor && a.constructor.name) || 'Animation',
      name: 'animationName' in a ? a.animationName : ('transitionProperty' in a ? a.transitionProperty : ''),
      // Per-iteration duration: this is the number that says "does this move",
      // and it is what the 0.001ms reduced-motion override drives to zero.
      durationMs: Number(timing.duration),
      activeDurationMs: Number(timing.activeDuration),
      iterations: Number(timing.iterations),
      playState: a.playState,
      selector: isEl ? inPageCssPath(target) : String(target),
      visible: isEl
        ? target.getClientRects().length > 0 && cs.visibility !== 'hidden' && Number(cs.opacity) > 0
        : true,
    });
  }
  return {
    animations,
    scrollBehavior: getComputedStyle(document.documentElement).scrollBehavior,
    scrollSelector: 'html',
  };
}

function inPageCollectImages() {
  const out = [];
  for (const el of document.querySelectorAll('img, input[type=image]')) {
    const isImg = el.tagName === 'IMG';
    const cs = getComputedStyle(el);
    // Effective visibility: a cross-faded-out frame or a display:none ancestor
    // must not be reported as a broken image.
    let shown = el.getClientRects().length > 0 && cs.visibility !== 'hidden' && Number(cs.opacity) > 0;
    for (let p = el.parentElement; p && shown; p = p.parentElement) {
      const ps = getComputedStyle(p);
      if (ps.display === 'none' || ps.visibility === 'hidden' || Number(ps.opacity) === 0) shown = false;
    }
    const src = isImg
      ? (el.currentSrc || el.src || el.getAttribute('data-src') || '')
      : (el.getAttribute('src') || '');
    out.push({
      selector: inPageCssPath(el),
      tag: el.tagName.toLowerCase(),
      hasAlt: el.hasAttribute('alt'),
      alt: el.getAttribute('alt'),
      shown,
      complete: isImg ? el.complete : true,
      naturalWidth: isImg ? el.naturalWidth : 0,
      src: String(src).slice(-90),
    });
  }
  return out;
}

function inPageBundleSource() {
  return [
    'var CFG = ' + JSON.stringify(PAGE_CFG) + ';',
    inPageCssPath,
    inPageParseColor,
    inPageHex,
    inPageOver,
    inPageEffectiveAlpha,
    inPageInlineInText,
    inPageTruncate,
    inPageInsideDisabledControl,
    inPageMediaShown,
    inPageOverMedia,
    inPageGradientStops,
    inPageBgImageLayers,
    inPageBgLayer,
    inPagePaintLayers,
    inPageCompositeLayer,
    inPageDedupeColors,
    inPageBgVariants,
    inPageCollectTargets,
    inPageCollectText,
    inPageCollectOverflow,
    inPageCollectMotion,
    inPageCollectImages,
    'window.__a11yAudit = {'
      + ' collectTargets: inPageCollectTargets,'
      + ' collectText: inPageCollectText,'
      + ' collectOverflow: inPageCollectOverflow,'
      + ' collectMotion: inPageCollectMotion,'
      + ' collectImages: inPageCollectImages,'
      + ' cfg: CFG };',
  ].join('\n\n');
}

const call = (page, fn) => page.evaluate((name) => window.__a11yAudit[name](), fn);

// ---------------------------------------------------------------------------
// Views exercised by the gate. `setup` runs on one shared page, so the whole
// audit costs a single navigation.
// ---------------------------------------------------------------------------
const STATES = [
  { name: 'timeline', setup: async () => {} },
  { name: 'objects', setup: async (page) => { await clickIfPresent(page, 'button[data-filter="objects"]'); } },
  { name: 'motion', setup: async (page) => { await clickIfPresent(page, 'button[data-filter="all"]'); } },
  {
    name: 'lightbox',
    setup: async (page) => {
      const opened = await page.evaluate(() => {
        const img = document.querySelector('.image-card img, .visit-card img, .card img');
        const card = img && (img.closest('.image-card') || img.closest('.visit-card') || img.closest('.card'));
        if (!card) return false;
        card.click();
        return true;
      }).catch(() => false);
      if (opened) await page.waitForSelector('#lightbox.active', { timeout: 3000 }).catch(() => {});
      await page.waitForTimeout(400);
    },
    teardown: async (page) => { await page.keyboard.press('Escape').catch(() => {}); },
  },
  {
    name: 'settings',
    setup: async (page) => {
      await clickIfPresent(page, '#btn-manage-blacklist');
      await page.waitForTimeout(500);
    },
    teardown: async (page) => { await page.keyboard.press('Escape').catch(() => {}); },
  },
  {
    name: 'status',
    setup: async (page) => {
      await clickIfPresent(page, '#btn-system-status');
      await page.waitForSelector('#system-status-content', { timeout: 4000 }).catch(() => {});
      await page.waitForTimeout(600);
    },
    teardown: async (page) => { await page.keyboard.press('Escape').catch(() => {}); },
  },
];

// Views measured for horizontal overflow (the three a user meets first).
const OVERFLOW_VIEWS = [
  { name: 'timeline', setup: async (page) => { await clickIfPresent(page, 'button[data-filter="events"]'); } },
  { name: 'motion', setup: async (page) => { await clickIfPresent(page, 'button[data-filter="all"]'); } },
  {
    name: 'settings',
    setup: async (page) => { await clickIfPresent(page, '#btn-manage-blacklist'); },
    teardown: async (page) => { await page.keyboard.press('Escape').catch(() => {}); },
  },
];

async function clickIfPresent(page, selector) {
  const el = await page.$(selector);
  if (!el) return false;
  await el.click({ timeout: 3000 }).catch(() => {});
  await page.waitForTimeout(700);
  return true;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Contrast ratios are computed in Node from the colours the page collected, so
// the audit and --selfcheck share one implementation of the WCAG formula.
//
// A plain record carries one fgRgb/bgRgb pair. A record measured over a
// gradient carries bgVariants -- one candidate per gradient stop composited
// over the layers beneath it -- and the WORST one is the number the gate acts
// on, because that is the case a user might actually be looking at.
function worstCasePair(rec) {
  const variants = rec.bgVariants;
  if (!variants || !variants.length) {
    if (rec.fgRgb && rec.bgRgb) {
      return { ratio: contrastRatio(rec.fgRgb, rec.bgRgb), fg: rec.fg, bg: rec.bg };
    }
    return typeof rec.ratio === 'number' ? { ratio: rec.ratio, fg: rec.fg, bg: rec.bg } : null;
  }
  let worst = null;
  for (const v of variants) {
    const ratio = contrastRatio(v.fgRgb, v.bgRgb);
    if (!worst || ratio < worst.ratio) worst = { ratio, fg: v.fg, bg: v.bg, fgRgb: v.fgRgb, bgRgb: v.bgRgb };
  }
  return worst;
}

function withRatios(text) {
  for (const t of text) {
    if (t.skip || (!t.bgVariants && !t.fgRgb)) continue;
    const worst = worstCasePair(t);
    t.ratio = worst.ratio;
    // Collapse to the worst-case pair so the finding message, the JSON report
    // and the coverage line all quote one number: the one that fails.
    t.fg = worst.fg;
    t.bg = worst.bg;
    t.fgRgb = worst.fgRgb || t.fgRgb;
    t.bgRgb = worst.bgRgb || t.bgRgb;
  }
  return text;
}

function stamp(records, state) {
  for (const r of records) if (!r.state) r.state = state;
  return records;
}

// ---------------------------------------------------------------------------
// Safety rails: the gate must never touch a live camera path.
// ---------------------------------------------------------------------------
function refuse(msg, hint) {
  process.stderr.write('a11y_audit: REFUSING TO RUN\n  ' + msg + '\n'
    + (hint ? '  ' + hint + '\n' : ''));
  process.exit(2);
}

function checkFixturesRoot(dir) {
  let real;
  try {
    real = fs.realpathSync(dir);
  } catch {
    refuse('fixtures root ' + dir + ' does not exist', 'pass --fixtures DIR pointing at a synthetic gallery');
  }
  const low = real.toLowerCase();
  for (const bad of THRESHOLDS.forbiddenRoots) {
    if (low === bad || low.startsWith(bad + '/') || low.startsWith(bad + '\\')) {
      refuse('fixtures root resolves to ' + real + ', which is under ' + bad,
        'this gate may only read synthetic fixtures, never a live camera directory');
    }
  }
  if (!fs.existsSync(path.join(real, 'images.json'))) {
    refuse('fixtures root ' + real + ' has no images.json, so it is not a gallery fixture tree',
      'run `python3 tools/screenshots/make_fixtures.py`, or pass --fixtures');
  }
  return real;
}

function checkUrl(raw) {
  let u;
  try {
    u = new URL(raw);
  } catch {
    refuse('--url ' + raw + ' is not a URL');
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') refuse('--url ' + raw + ' is not http(s)');
  if (!/^(127\.0\.0\.1|localhost|\[::1\]|::1)$/.test(u.hostname) && !process.env.A11Y_AUDIT_ALLOW_REMOTE) {
    refuse('--url ' + u.href + ' is not a loopback origin',
      'start tools/screenshots/proxy.py and point the audit at it, or set A11Y_AUDIT_ALLOW_REMOTE=1 if you really mean it');
  }
  return u.href;
}

function fetchJson(url) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: 5000 }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { body += c; });
      res.on('end', () => {
        try {
          resolve(JSON.parse(body));
        } catch {
          resolve(null);
        }
      });
    });
    req.on('error', () => resolve(null));
    req.on('timeout', () => { req.destroy(); resolve(null); });
  });
}

// ---------------------------------------------------------------------------
// Audit
// ---------------------------------------------------------------------------
let tearingDown = false;

// The app resolves its appearance from localStorage on DOMContentLoaded, so
// the preference must be in storage before the page's own scripts run. This
// init script is registered BEFORE the collector bundle for exactly that
// reason; an evaluate() after load would be too late for the first paint.
function themeInitScript(theme) {
  return 'try { window.localStorage.setItem(' + JSON.stringify(THRESHOLDS.themeStorageKey)
    + ', ' + JSON.stringify(theme) + '); } catch (e) { /* storage can be restricted */ }';
}

async function newPage(browser, contextOptions, theme) {
  const ctx = await browser.newContext(contextOptions);
  if (theme) await ctx.addInitScript({ content: themeInitScript(theme) });
  await ctx.addInitScript({ content: inPageBundleSource() });
  const page = await ctx.newPage();
  return { ctx, page };
}

async function readAppliedTheme(page) {
  return page.evaluate(() => document.documentElement.getAttribute('data-theme') || '');
}

function watch(page, bucket, stateRef) {
  page.on('response', (r) => {
    bucket.http.push({ state: stateRef(), url: r.url(), status: r.status() });
  });
  page.on('requestfailed', (r) => {
    const f = r.failure() || {};
    // Aborts caused by our own teardown are not defects.
    if (tearingDown && /ERR_ABORTED|ERR_CONNECTION_RESET|ERR_TIMED_OUT/.test(f.errorText || '')) return;
    bucket.http.push({ state: stateRef(), url: r.url(), failed: true, failure: f.errorText || 'unknown' });
  });
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const loc = m.location() || {};
    bucket.http.push({
      state: stateRef(),
      url: 'console',
      console: String(m.text()).slice(0, 300),
      consoleSource: String(loc.url || 'page').slice(0, 160),
    });
  });
  page.on('pageerror', (e) => {
    bucket.http.push({ state: stateRef(), url: 'pageerror', pageError: String(e.message).slice(0, 300) });
  });
}

async function settle(page) {
  // Never networkidle: the SSE stub holds /api/events open for 120s.
  await page.waitForLoadState('domcontentloaded').catch(() => {});
  // Neutralise finite animations so a half-faded or mid-pulse colour is never
  // what gets measured; infinite loops are left alone. The reduced-motion
  // check runs in its own context, before this, on a real animation list.
  await page.evaluate(() => {
    for (const a of document.getAnimations()) {
      if (!a.effect || !a.effect.getComputedTiming) continue;
      const t = a.effect.getComputedTiming();
      if (t.iterations === Infinity) continue;
      a.pause();
      a.currentTime = Math.max(0, Number(t.endTime) || 0);
    }
    // Kick lazy images into loading so the broken-image check is meaningful.
    const sc = document.scrollingElement || document.body;
    if (sc) {
      sc.scrollTop = sc.scrollHeight;
      sc.scrollTop = 0;
    }
  }).catch(() => {});
  await sleep(300);
}

function summarise(rec) {
  const checked = rec.text.filter((t) => !t.skip);
  const targets = rec.targets.filter((t) => t.rendered);
  const sizes = targets.filter((t) => !t.inlineInText && !t.fullWidth)
    .map((t) => Math.min(t.w, t.h));
  const ratios = checked.map(worstCasePair).filter((w) => w).map((w) => w.ratio);
  // Per-reason skip histogram: a coverage line that says "84 skipped" without
  // saying why is how a vacuous pass hides (issue #116).
  const skipReasons = {};
  for (const t of rec.text) {
    if (t.skip) skipReasons[t.skip] = (skipReasons[t.skip] || 0) + 1;
  }
  // WCAG 1.4.3 exempts a disabled control's label, so it leaves the coverage
  // denominator instead of counting as an unmeasured node. Same number the
  // coverage gate uses, so the JSON per state and the finding agree.
  const textExempt = exemptSkipCount(skipReasons);
  const measurable = rec.text.length - textExempt;
  return {
    targets: rec.targets.length,
    renderedTargets: targets.length,
    smallestTarget: sizes.length ? Math.round(Math.min(...sizes) * 100) / 100 : null,
    textNodes: rec.text.length,
    textChecked: checked.length,
    textSkipped: rec.text.length - checked.length,
    textExempt,
    skipReasons,
    coverage: measurable ? checked.length / measurable : 0,
    minContrast: ratios.length ? Math.round(Math.min(...ratios) * 100) / 100 : null,
    images: rec.images.length,
    brokenImages: rec.images.filter(imageBroken).length,
  };
}

// The width sweep needs no palette, so it runs once (in the first requested
// theme) instead of doubling the runtime per theme.
async function runOverflowSweep(browser, theme, url, net, coverage, stateRef, viewport) {
  const { ctx, page } = await newPage(browser, { viewport, deviceScaleFactor: 1 }, theme);
  watch(page, net, () => stateRef.name);
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#filter-tabs, .image-card, .visit-card, .card', { timeout: 10000 }).catch(() => {});
  await settle(page);
  for (const view of OVERFLOW_VIEWS) {
    stateRef.name = 'overflow/' + view.name;
    await view.setup(page);
    await settle(page);
    for (const width of THRESHOLDS.overflowWidths) {
      await page.setViewportSize({ width, height: 900 });
      await page.waitForTimeout(220);
      const rec = stamp([await call(page, 'collectOverflow')], view.name);
      rec[0].width = width;
      net.overflow.push(...rec);
    }
    await page.setViewportSize(viewport);
    if (view.teardown) await view.teardown(page);
    await page.waitForTimeout(200);
  }
  coverage['overflow/widths'] = {
    theme,
    widths: THRESHOLDS.overflowWidths.length,
    views: OVERFLOW_VIEWS.length,
  };
  tearingDown = true;
  await ctx.close();
  tearingDown = false;
}

async function runAudit(opts) {
  const { chromium } = require('playwright');
  const url = checkUrl(opts.url);
  const fixtures = checkFixturesRoot(opts.fixtures);

  // The origin must be the fixture stub, not a live api_server.
  const health = await fetchJson(new URL(url).origin + '/api/health');
  if (!health || health.fixture !== true) {
    refuse('/api/health did not report the fixture stub (got ' + JSON.stringify(health) + ')',
      'the audit only runs against tools/screenshots/proxy.py, never a live box');
  }

  const browser = await chromium.launch({ headless: true });
  const t0 = Date.now();
  const net = { http: [], targets: [], text: [], images: [], overflow: [], motion: [], theme: [] };
  const coverage = {};
  const themes = [];
  const stateRef = { name: 'load' };
  const viewport = { width: 1440, height: 900 };
  const failures = [];
  const wantThemes = opts.themes;

  try {
    // ---- per-theme: every application state, both Light and Dark ---------
    for (const theme of wantThemes) {
      const { ctx, page } = await newPage(browser, { viewport, deviceScaleFactor: 1 }, theme);
      watch(page, net, () => stateRef.name);
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector('#filter-tabs, .image-card, .visit-card, .card', { timeout: 10000 }).catch(() => {});
      await page.waitForFunction(() => !!document.documentElement.getAttribute && document.documentElement.getAttribute('data-theme'), null, { timeout: 5000 }).catch(() => {});
      await settle(page);
      const applied = await readAppliedTheme(page);
      net.theme.push({ state: 'theme/' + theme, theme, applied });

      let textNodes = 0;
      let textChecked = 0;
      let textSkipped = 0;
      const skipReasons = {};
      for (const state of STATES) {
        stateRef.name = theme + '/' + state.name;
        await state.setup(page);
        await settle(page);
        const targets = stamp(await call(page, 'collectTargets'), stateRef.name);
        const text = stamp(await call(page, 'collectText'), stateRef.name);
        const images = stamp(await call(page, 'collectImages'), stateRef.name);
        net.targets.push(...targets);
        net.text.push(...text);
        net.images.push(...images);
        const sum = summarise({ targets, text, images });
        coverage[stateRef.name] = sum;
        textNodes += sum.textNodes;
        textChecked += sum.textChecked;
        textSkipped += sum.textSkipped;
        for (const r of Object.keys(sum.skipReasons)) {
          skipReasons[r] = (skipReasons[r] || 0) + sum.skipReasons[r];
        }
        if (state.teardown) await state.teardown(page);
        await page.waitForTimeout(200);
      }
      const textExempt = exemptSkipCount(skipReasons);
      const measurable = textNodes - textExempt;
      themes.push({
        theme,
        applied,
        textNodes,
        textChecked,
        textSkipped,
        textExempt,
        skipReasons,
        coverage: measurable ? textChecked / measurable : 0,
      });

      tearingDown = true;
      await ctx.close();
      tearingDown = false;
    }

    // ---- horizontal overflow at every width --------------------------------
    // One sweep, in the first requested theme: the widths and the page chrome
    // are the same in both, and re-running it per theme roughly doubles the
    // runtime for a measurement that does not depend on the palette.
    await runOverflowSweep(browser, wantThemes[0], url, net, coverage, stateRef, viewport);

    // ---- prefers-reduced-motion: own context, requested before load ----
    stateRef.name = 'reduced-motion';
    const rm = await newPage(browser, { viewport, reducedMotion: 'reduce' }, wantThemes[0]);
    watch(rm.page, net, () => stateRef.name);
    await rm.page.goto(url, { waitUntil: 'domcontentloaded' });
    await rm.page.waitForSelector('#filter-tabs, .image-card, .visit-card, .card', { timeout: 10000 }).catch(() => {});
    await settle(rm.page);
    for (const view of [
      { name: 'timeline', setup: async () => {} },
      { name: 'motion', setup: async (p) => { await clickIfPresent(p, 'button[data-filter="all"]'); } },
      {
        name: 'settings',
        setup: async (p) => { await clickIfPresent(p, '#btn-manage-blacklist'); },
        teardown: async (p) => { await p.keyboard.press('Escape').catch(() => {}); },
      },
    ]) {
      await view.setup(rm.page);
      await settle(rm.page);
      const m = await call(rm.page, 'collectMotion');
      m.state = view.name;
      net.motion.push(m);
      coverage['reduced-motion/' + view.name] = {
        animations: m.animations.length,
        longestMs: m.animations.reduce((a, x) => Math.max(a, x.visible ? x.durationMs : 0), 0),
        scrollBehavior: m.scrollBehavior,
      };
      if (view.teardown) await view.teardown(rm.page);
      await rm.page.waitForTimeout(200);
    }
    tearingDown = true;
    await rm.ctx.close();
    tearingDown = false;
  } finally {
    tearingDown = true;
    await browser.close();
  }

  for (const check of CHECKS) {
    const key = {
      'http-clean': 'http',
      'target-size': 'targets',
      'text-contrast': 'text',
      'text-contrast-coverage': 'text',
      'h-overflow': 'overflow',
      'reduced-motion': 'motion',
      'image-alt': 'images',
      'theme-applied': 'theme',
    }[check.id];
    failures.push(...check.run(withRatios(net[key])));
  }

  const seconds = ((Date.now() - t0) / 1000).toFixed(1);
  const result = {
    url,
    fixtures,
    seconds,
    themes: wantThemes,
    checks: CHECKS.map((c) => c.id),
    failures,
    coverage,
    themeRuns: themes,
    thresholds: THRESHOLDS,
  };
  report(result);
  if (process.env.A11Y_AUDIT_JSON) {
    try {
      fs.writeFileSync(process.env.A11Y_AUDIT_JSON, JSON.stringify(result, null, 2));
      process.stdout.write('a11y_audit: wrote ' + process.env.A11Y_AUDIT_JSON + '\n');
    } catch (e) {
      process.stderr.write('a11y_audit: could not write JSON report: ' + e.message + '\n');
    }
  }
  return failures;
}

function report(res) {
  const lines = [];
  lines.push('a11y_audit: ' + res.url);
  lines.push('a11y_audit: fixtures=' + res.fixtures + ' elapsed=' + res.seconds + 's');
  // Coverage per theme first: this is the line that says whether the contrast
  // assertion measured anything at all, which is the whole lesson of #116.
  for (const t of res.themeRuns || []) {
    // The exempt figure rides next to the coverage number: WCAG 1.4.3 exempts a
    // disabled control's label, so those nodes are NOT in the denominator.
    const exempt = t.textExempt === undefined ? exemptSkipCount(t.skipReasons || {}) : t.textExempt;
    const measurable = t.textNodes - exempt;
    lines.push('  theme ' + t.theme + ' (resolved html[data-theme]="' + (t.applied || 'unset') + '") text '
      + t.textChecked + '/' + t.textNodes + ' checked, coverage '
      + (measurable ? Math.round(t.coverage * 1000) / 10 + '%' : 'n/a')
      + ', exempt-disabled: ' + exempt
      + (Object.keys(t.skipReasons).length
        ? ', skips ' + JSON.stringify(t.skipReasons)
        : ', no skips'));
  }
  for (const k of Object.keys(res.coverage)) {
    lines.push('  coverage ' + k + ' ' + JSON.stringify(res.coverage[k]));
  }
  const byCheck = new Map();
  for (const f of res.failures) byCheck.set(f.check, (byCheck.get(f.check) || 0) + 1);
  for (const check of CHECKS) {
    const n = byCheck.get(check.id) || 0;
    lines.push('  ' + (n ? 'FAIL' : 'pass') + ' ' + check.id
      + ' (' + n + ' finding' + (n === 1 ? '' : 's') + ') -- ' + check.what);
  }
  if (res.failures.length) {
    // One defect often shows up in several states (the same chip overflows in
    // every view at 320px). Group identical findings so the output names the
    // defect once and lists where it was seen; the JSON keeps every record.
    const groups = new Map();
    for (const f of res.failures) {
      const key = [f.check, f.selector, f.detail].join('|');
      if (!groups.has(key)) groups.set(key, { f, states: [] });
      groups.get(key).states.push(f.state);
    }
    lines.push('');
    lines.push('a11y_audit: ' + res.failures.length + ' assertion failure(s) in '
      + groups.size + ' distinct defect(s):');
    let n = 0;
    for (const g of groups.values()) {
      if (n >= 60) break;
      n += 1;
      const states = [...new Set(g.states)];
      lines.push('  [' + g.f.check + '] seen in: ' + states.join(', '));
      lines.push('      selector: ' + g.f.selector);
      lines.push('      ' + g.f.detail);
      if (g.f.fix) lines.push('      fix: ' + g.f.fix);
    }
    if (groups.size > n) lines.push('  ... and ' + (groups.size - n) + ' more');
    lines.push('');
    lines.push('a11y_audit: FAIL');
  } else {
    lines.push('');
    lines.push('a11y_audit: OK -- all ' + CHECKS.length + ' assertion families pass');
  }
  process.stdout.write(lines.join('\n') + '\n');
}

// ---------------------------------------------------------------------------
// --selfcheck: prove, with a real browser, that the gate is not vacuous.
//
// Runs the same collectors and the same checkers over inline pages: one
// seeded with a 6px target, a 3:1 text pair (plain and on a GRADIENT, the case
// #116 silently skipped), a 2000px block, an <img> with no alt, a broken
// image, a refused request and an uncaught exception -- which MUST fail -- and
// one clean page, which MUST pass. A third pair of pages proves the
// reduced-motion threshold. A fourth proves the coverage gate: a page where
// every text node is skipped MUST fail with text-contrast-coverage, and the
// same page with the url()-photo waivers MUST pass.
// ---------------------------------------------------------------------------
const UNREACHABLE = 'http://127.0.0.1:9/seed-missing.json';
// A real 1x1 GIF. (The shorter R0lGODlhAQABAAAAACw= is truncated, so Chromium
// reports naturalWidth 0 -- which would make every selfcheck image "broken".)
const PIXEL = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

const SEEDED_PAGE = `<!doctype html><html><head><style>
  body { margin:0; background:#0f172a; font-family:sans-serif; }
  .tiny { display:inline-block; width:6px; height:6px; background:#3b82f6; border:0; padding:0; }
  .faint { color:#8a8a8a; background:#ffffff; }
  .faint-grad { color:#8a8a8a; background-image:linear-gradient(90deg,#ffffff,#000000); }
  /* The two exemption seeds keep the SAME faint pair as the paragraphs above,
     so a reader can see what each exemption changed and what it did not. */
  /* :not(.tiny) keeps the 44px floor off the 6x6 seed, so the target-size
     assertion still has something to find. */
  button:not(.tiny) { min-width:44px; min-height:44px; }
  .photo-wrap { position:relative; width:220px; height:120px; }
  .photo-wrap img { display:block; width:220px; height:120px; }
  .photo-wrap p { position:absolute; left:0; top:0; margin:0; }
  .readable p { color:#0f172a; background:#ffffff; margin:2px 0; }
  .spinner { width:12px; height:12px; animation: spin 700ms linear infinite; }
  .wide { width:2000px; height:20px; background:#334155; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style></head><body>
  <button class="tiny" id="seed-tiny" aria-label="seeded undersized target"></button>
  <p class="faint">seeded low contrast body text</p>
  <p class="faint-grad">seeded low contrast text over a gradient</p>
  <!-- WCAG 1.4.3 exemption 1: the label of an inactive control. The enabled
       twin right below it is NOT exempt and must still be reported. -->
  <button class="faint" id="seed-disabled" disabled>disabled button label</button>
  <button class="faint" id="seed-aria-disabled" aria-disabled="true">aria disabled button label</button>
  <button class="faint" id="seed-enabled">enabled button label</button>
  <!-- WCAG 1.4.3 exemption 2: text layered over a picture. The paragraph it is
       styled exactly like must still be measured. -->
  <p class="faint" id="seed-plain">the same faint text with no picture under it</p>
  <div class="photo-wrap">
    <img id="seed-photo" alt="a photo the caption is layered over" src="${PIXEL}">
    <p class="faint" id="seed-overlay">caption layered over the photo</p>
  </div>
  <!-- Readable control text, so the three exempt nodes above stay a minority
       and this state still clears the 80% coverage floor. -->
  <div class="readable" id="seed-readable">
    <p>readable seeded line one</p>
    <p>readable seeded line two</p>
    <p>readable seeded line three</p>
    <p>readable seeded line four</p>
    <p>readable seeded line five</p>
    <p>readable seeded line six</p>
    <p>readable seeded line seven</p>
    <p>readable seeded line eight</p>
    <p>readable seeded line nine</p>
  </div>
  <div class="spinner" id="seed-spin"></div>
  <img id="seed-broken" alt="seeded broken image" style="width:64px;height:48px" src="/seed-missing.png">
  <img id="seed-no-alt" style="width:64px;height:48px" src="${PIXEL}">
  <div class="wide" id="seed-wide"></div>
  <script>
    fetch(${JSON.stringify(UNREACHABLE)}).catch(function () {});
    console.error('seeded console error');
    setTimeout(function () { throw new Error('seeded uncaught'); }, 0);
  </script>
</body></html>`;

const CLEAN_PAGE = `<!doctype html><html><head><style>
  body { margin:0; padding:8px; background:#0f172a; color:#e2e8f0; font-family:sans-serif; }
  /* A gradient background must be MEASURED, not skipped: the clean page has to
     prove that a readable pair over both stops still passes (issue #116). */
  .hero { background-image:linear-gradient(180deg,#0f172a,#1e293b); }
  button { min-width:44px; min-height:44px; }
  a { color:#e2e8f0; display:inline-block; padding:10px 12px; }
  p { margin:8px 0; }
</style></head><body>
  <button id="ok-btn">Send</button>
  <a href="#top" id="ok-link">A link on its own line</a>
  <p>Readable body text on a dark surface, comfortably above 4.5 to 1.</p>
  <div class="hero"><p id="ok-grad">Readable text over a gradient, on both stops.</p></div>
  <img alt="a described image" style="width:64px;height:48px" src="${PIXEL}">
</body></html>`;

// Every text node over a url() photo. Before #116 this page was invisible to
// the gate (the contrast check had nothing to measure); now it must fail the
// coverage gate -- unless the nodes are on the documented exception list.
const ALL_SKIPPED_PAGE = `<!doctype html><html><head><style>
  body { margin:0; background:#0f172a; color:#e2e8f0; font-family:sans-serif; }
  /* Both text nodes sit on the photo, so 2 of 2 are unmeasurable. The layers
     beneath the photo stay CSS-coloured: only url() images are skipped. */
  .photo { width:220px; height:140px; padding:8px; background:#101f30 url('${PIXEL}') center/cover; }
</style></head><body>
  <div class="photo">
    <h1 id="photo-heading">heading over a photo</h1>
    <p id="photo-caption">caption painted over a photo</p>
  </div>
</body></html>`;

// The app's own reduced-motion override: 0.001ms, i.e. 1e-06s. A string
// comparison against "0s" would call this motion; a numeric one does not.
const QUIET_PAGE = `<!doctype html><html><head><style>
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.001ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.001ms !important;
      scroll-behavior: auto !important;
    }
  }
  .spin { width:12px; height:12px; animation: spin 700ms linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style></head><body><div class="spin" id="quiet-spin"></div></body></html>`;

const LOUD_PAGE = `<!doctype html><html><head><style>
  .spin { width:12px; height:12px; animation: spin 700ms linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  html { scroll-behavior: smooth; }
</style></head><body><div class="spin" id="loud-spin"></div></body></html>`;

async function selfcheck() {
  const { chromium } = require('playwright');
  const browser = await chromium.launch({ headless: true });
  const problems = [];

  const open = async (ctxOptions, html) => {
    const { ctx, page } = await newPage(browser, Object.assign({ viewport: { width: 1440, height: 900 } }, ctxOptions));
    await page.goto('data:text/html;charset=utf-8,' + encodeURIComponent(html), { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(600);
    return { ctx, page };
  };

  const runOne = async (label, ctxOptions, html) => {
    const net = [];
    const { ctx, page } = await open(ctxOptions, html);
    watch(page, { http: net }, () => label);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(700);
    const recs = {
      targets: stamp(await call(page, 'collectTargets'), label),
      text: withRatios(stamp(await call(page, 'collectText'), label)),
      overflow: stamp([await call(page, 'collectOverflow')], label),
      motion: stamp([await call(page, 'collectMotion')], label),
      images: stamp(await call(page, 'collectImages'), label),
    };
    await ctx.close();
    const found = {};
    for (const check of CHECKS) {
      const key = {
        'http-clean': net,
        'target-size': recs.targets,
        'text-contrast': recs.text,
        'text-contrast-coverage': recs.text,
        'h-overflow': recs.overflow,
        'reduced-motion': recs.motion,
        'image-alt': recs.images,
      }[check.id];
      found[check.id] = check.run(key || []);
    }
    return found;
  };

  // The page-side config must be the encoded WCAG values, or the collectors
  // would measure with stale numbers.
  {
    const { ctx, page } = await open({}, CLEAN_PAGE);
    const cfg = await page.evaluate(() => window.__a11yAudit.cfg);
    for (const k of Object.keys(PAGE_CFG)) {
      if (cfg[k] !== PAGE_CFG[k]) {
        problems.push('in-page CFG.' + k + ' is ' + cfg[k] + ', expected ' + PAGE_CFG[k]);
      }
    }
    await ctx.close();
  }

  // A gate that fails everything is as useless as one that passes everything.
  const clean = await runOne('clean', {}, CLEAN_PAGE);
  for (const [id, f] of Object.entries(clean)) {
    if (f.length) {
      problems.push('clean page must pass ' + id + ' but got ' + f.length + ' finding(s): ' + f[0].detail
        + ' [' + f[0].selector + ']');
    }
  }

  const dirty = await runOne('seeded', {}, SEEDED_PAGE);
  const expectMin = {
    'http-clean': 2,
    'target-size': 1,
    'text-contrast': 2,
    'h-overflow': 1,
    'image-alt': 2,
  };
  for (const [id, min] of Object.entries(expectMin)) {
    const got = (dirty[id] || []).length;
    if (got < min) {
      problems.push('seeded page: ' + id + ' found ' + got + ' finding(s), expected >= ' + min
        + ' -- the gate is vacuous for this assertion');
    }
  }
  const mustName = [
    ['target-size', '#seed-tiny', 'the 6x6 button'],
    ['text-contrast', '.faint', 'the 3.45:1 body-text pair'],
    ['text-contrast', '.faint-grad', 'the 3.45:1 pair over a GRADIENT (the #116 skip)'],
    ['text-contrast', '#seed-enabled', 'the ENABLED twin of the disabled button (1.4.3 exempts only inactive components)'],
    ['text-contrast', '#seed-plain', 'the faint paragraph with NO picture under it (the overlay exemption must not swallow it)'],
    ['h-overflow', '#seed-wide', 'the 2000px block'],
    ['image-alt', '#seed-no-alt', 'the img with no alt'],
    ['image-alt', '#seed-broken', 'the broken image'],
  ];
  for (const [id, sel, why] of mustName) {
    if (!(dirty[id] || []).some((f) => String(f.selector).includes(sel))) {
      problems.push('seeded page: ' + id + ' did not name ' + sel + ' (' + why + ')');
    }
  }
  // The exempt seeds must not be findings, and must not exist as nodes the
  // contrast check silently dropped either: each has to come back as a COUNTED
  // skip, which is what puts it in the histogram and the coverage denominator.
  {
    const { ctx, page } = await open({}, SEEDED_PAGE);
    const exempt = stamp(await call(page, 'collectText'), 'seeded');
    await ctx.close();
    const forSel = (sel) => exempt.find((r) => String(r.selector).includes(sel));
    const expectedSkips = [
      ['#seed-disabled', 'disabled-control', 'the disabled button label'],
      ['#seed-aria-disabled', 'disabled-control', 'the aria-disabled button label'],
      ['#seed-overlay', 'over-image', 'the caption layered over the photo'],
    ];
    for (const [sel, reason, why] of expectedSkips) {
      const rec = forSel(sel);
      if (!rec) problems.push('seeded page: no text record for ' + sel + ' (' + why + ')');
      else if (rec.skip !== reason) {
        problems.push('seeded page: ' + why + ' must be a counted skip ' + reason + ', got '
          + (rec.skip ? 'skip ' + rec.skip : 'a MEASURED record -- the exemption is not wired up'));
      }
      if ((dirty['text-contrast'] || []).some((f) => String(f.selector).includes(sel))) {
        problems.push('seeded page: ' + why + ' must not be a contrast finding');
      }
    }
    const agg = summariseCoverage(exempt, SKIP_EXCEPTIONS);
    if (agg.skipReasons['disabled-control'] !== 2 || agg.skipReasons['over-image'] !== 1) {
      problems.push('seeded page: the skip histogram must name both exemptions, got '
        + skipHistogramText(agg.skipReasons));
    }
    // The floor still counts them: they are nodes the state did not measure.
    if (agg.textSkipped !== 3 || agg.textChecked !== agg.textNodes - 3) {
      problems.push('seeded page: the three exempt nodes must be counted as skipped, got '
        + agg.textSkipped + ' skipped of ' + agg.textNodes);
    }
    if (checkTextContrastCoverage(exempt, SKIP_EXCEPTIONS).length) {
      problems.push('seeded page: three exempt nodes must keep the state above the '
        + pct(THRESHOLDS.minTextCoverage) + '% coverage floor, got ' + pct(agg.coverage) + '%');
    }
  }
  // The gradient text must be MEASURED, not skipped: the finding has to carry
  // the worst-case ratio across its stops (#ffffff -> #000000 puts the same
  // #8a8a8a fg at 3.45:1 on white and 6.09:1 on black; 3.45 is what fails).
  const gradFinding = (dirty['text-contrast'] || []).find((f) => String(f.selector).includes('.faint-grad'));
  if (!gradFinding) {
    problems.push('seeded page: the low-contrast text over a gradient was not reported --'
      + ' this is exactly the skip issue #116 shipped');
  } else if (!/measured 3\.45:1/.test(gradFinding.detail)) {
    problems.push('seeded page: the gradient finding must quote the WORST-CASE ratio across the'
      + ' gradient stops (3.45:1), got: ' + gradFinding.detail);
  }

  // Coverage gate: a state where every text node is skipped MUST fail, with
  // the skip-reason histogram in the message; the same page MUST pass when the
  // nodes are on the documented url()-photo exception list.
  const allSkipped = await runOne('all-skipped', {}, ALL_SKIPPED_PAGE);
  const coverageFindings = allSkipped['text-contrast-coverage'] || [];
  if (!coverageFindings.length) {
    problems.push('all-skipped page: text-contrast-coverage did not fire -- a state where every'
      + ' text node is skipped would report a vacuous PASS (issue #116)');
  } else if (!coverageFindings.some((f) => /measured 0 of 2/.test(f.detail)
    && /background-url-image/.test(f.detail))) {
    problems.push('all-skipped page: the coverage finding must state that 0 of 2 text nodes were'
      + ' measured and name the skip reason, got: ' + coverageFindings[0].detail);
  }
  {
    const { ctx, page } = await open({}, ALL_SKIPPED_PAGE);
    const recs = withRatios(stamp(await call(page, 'collectText'), 'all-skipped'));
    await ctx.close();
    const waived = checkTextContrastCoverage(recs, [
      { selector: '#photo-caption', why: 'selfcheck fixture: painted over a url() photo' },
      { selector: '#photo-heading', why: 'selfcheck fixture: painted over a url() photo' },
    ]);
    if (waived.length) {
      problems.push('all-skipped page: the documented url()-photo exception list must waive the'
        + ' coverage gate, but got: ' + waived[0].detail);
    }
  }

  // Reduced motion: the 700ms spin must be caught, the 0.001ms override the
  // app actually ships must not be.
  const loud = await runOne('reduced-motion/seeded', { reducedMotion: 'reduce' }, LOUD_PAGE);
  if (!loud['reduced-motion'].some((f) => f.selector.includes('#loud-spin') && /700ms/.test(f.detail))) {
    problems.push('seeded reduced-motion page: the 700ms spin was not reported ('
      + loud['reduced-motion'].length + ' finding(s)) -- the motion assertion is vacuous');
  }
  const quiet = await runOne('reduced-motion/quiet', { reducedMotion: 'reduce' }, QUIET_PAGE);
  if (quiet['reduced-motion'].length) {
    problems.push('the 0.001ms reduced-motion override must pass, but got: '
      + quiet['reduced-motion'][0].detail);
  }

  for (const p of problems) process.stderr.write('a11y_audit selfcheck: ' + p + '\n');
  const total = (o) => Object.values(o).reduce((n, f) => n + f.length, 0);
  process.stdout.write('a11y_audit selfcheck: clean=' + total(clean) + ' finding(s), seeded='
    + total(dirty) + ', reduced-motion seeded=' + total(loud) + ', reduced-motion 0.001ms override='
    + total(quiet) + ', coverage gate=' + total(allSkipped) + ' finding(s) -> '
    + (problems.length ? 'FAIL' : 'OK') + '\n');
  await browser.close();
  return problems.length ? 1 : 0;
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------
// 'light' | 'dark' | 'both'. The default audits BOTH themes: PR #112's
// gradient made the light/dark difference visible, and a gate that only ever
// measured one of them had already missed the vacuous-pass regression.
function resolveThemes(value) {
  if (value === undefined || value === null || value === '' || value === 'both') return THRESHOLDS.themes.slice();
  if (value === 'light' || value === 'dark') return [value];
  return null;
}

function parseArgs(argv) {
  const opts = {
    url: process.env.A11Y_AUDIT_URL || DEFAULT_URL,
    fixtures: process.env.A11Y_AUDIT_FIXTURES || DEFAULT_FIXTURES,
    selfcheck: false,
    theme: process.env.A11Y_AUDIT_THEME || 'both',
  };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--selfcheck') opts.selfcheck = true;
    else if (a === '--url') opts.url = argv[++i];
    else if (a === '--fixtures') opts.fixtures = argv[++i];
    else if (a === '--theme') opts.theme = argv[++i];
    else if (a.startsWith('--theme=')) opts.theme = a.slice('--theme='.length);
    else if (a === '-h' || a === '--help') {
      const header = fs.readFileSync(__filename, 'utf8').split('*/')[0];
      process.stdout.write(header.replace(/^\/\/ ?/gm, ''));
      process.exit(0);
    } else {
      process.stderr.write('a11y_audit: unknown argument ' + a + '\n');
      process.exit(2);
    }
  }
  return opts;
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const themes = resolveThemes(opts.theme);
  if (!themes) {
    process.stderr.write('a11y_audit: --theme must be light, dark or both (got ' + opts.theme + ')\n');
    process.exit(2);
  }
  opts.themes = themes;
  if (opts.selfcheck) process.exit(await selfcheck());
  let failures;
  try {
    failures = await runAudit(opts);
  } catch (e) {
    process.stderr.write('a11y_audit: harness error: ' + (e && e.stack ? e.stack : e) + '\n');
    process.exit(3);
  }
  process.exit(failures.length ? 1 : 0);
}

if (require.main === module) main();

module.exports = {
  THRESHOLDS,
  PAGE_CFG,
  CHECKS,
  STATES,
  OVERFLOW_VIEWS,
  checkHttpClean,
  checkTargetSize,
  checkTextContrast,
  checkTextContrastCoverage,
  checkOverflow,
  checkReducedMotion,
  checkImages,
  checkThemeApplied,
  contrastRatio,
  relativeLuminance,
  imageBroken,
  inPageBundleSource,
  // Pure helpers behind the gradient measurement (unit-tested without a
  // browser by tests/test_a11y_coverage_gate.js).
  SKIP_EXCEPTIONS,
  inPageGradientStops,
  inPageBgImageLayers,
  inPageBgLayer,
  inPagePaintLayers,
  inPageCompositeLayer,
  inPageBgVariants,
  // The two 1.4.3 exemptions, same treatment: they take a fake document, so
  // the same suite runs them in Node.
  inPageInsideDisabledControl,
  inPageMediaShown,
  inPageOverMedia,
  worstCasePair,
  summarise,
  summariseCoverage,
  withRatios,
  resolveThemes,
};
