// Contract test: text must not paint in the low-contrast --accent on a tinted
// dark surface. Issue #67 — a browser computed-style audit found
// var(--accent) (#3b82f6) on var(--bg-tertiary) (#1e293b) = 3.98:1 where WCAG
// AA wants 4.5:1, in the active sidebar item, the active view tab, the sidebar
// count badge and the segmented control.
//
// The app already ships --accent-fg (#60a5fa, "readable accent on tinted dark
// bg") for exactly this. #3b82f6 stays the border/fill/chart colour, where the
// text-contrast rule does not apply. This test scans every CSS rule in
// index.html for the two offending *patterns* rather than naming the four
// selectors, so a new component reproducing the bug fails here.
//
// Run: node tests/test_a11y_contrast.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

// ---- tokens -------------------------------------------------------------
const rootBlock = html.match(/:root\s*\{([\s\S]*?)\n\s*\}/);
assert(rootBlock, 'expected a :root token block');
const tokens = {};
for (const m of rootBlock[1].matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
  tokens[m[1]] = m[2].trim().replace(/\/\*[\s\S]*?\*\//g, '').trim();
}
for (const t of ['--accent', '--accent-fg', '--bg-tertiary', '--bg-secondary', '--bg-primary']) {
  assert(tokens[t], `expected token ${t}`);
}

// ---- colour maths (WCAG 2.x relative luminance) -------------------------
function hex(c) {
  const h = c.replace('#', '');
  const full = h.length === 3 ? h.split('').map((x) => x + x).join('') : h;
  return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
}
function parseColor(c) {
  c = c.trim();
  let m = c.match(/^#([0-9a-fA-F]{3,8})$/);
  if (m) {
    const [r, g, b] = hex(m[1]);
    return { r, g, b, a: m[1].length === 8 ? parseInt(m[1].slice(6, 8), 16) / 255 : 1 };
  }
  m = c.match(/^rgba?\(([^)]+)\)$/);
  if (m) {
    const p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  }
  return null;
}
const resolve = (v) => {
  let out = v.trim();
  for (let i = 0; i < 6 && out.includes('var('); i++) {
    out = out.replace(/var\(\s*(--[a-z0-9-]+)\s*\)/g, (_, name) => tokens[name] || 'transparent');
  }
  return out;
};
const over = (fg, bg) => ({
  r: fg.r * fg.a + bg.r * (1 - fg.a),
  g: fg.g * fg.a + bg.g * (1 - fg.a),
  b: fg.b * fg.a + bg.b * (1 - fg.a),
  a: 1,
});
const lum = (c) => {
  const f = (v) => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
};
const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
};

// ---- rule scan ----------------------------------------------------------
// Every `{ ... }` declaration block in every <style> block, with its selector.
// `raw` keeps the token reference (so the patterns below read as authored) and
// `value` resolves var() chains for the numeric check.
const blocks = [];
for (const style of html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)) {
  for (const m of style[1].matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = m[1].trim().replace(/\s+/g, ' ');
    if (selector.startsWith('@') || selector.includes('%')) continue; // at-rules / keyframes
    const decls = {};
    for (const d of m[2].matchAll(/([-a-z]+)\s*:\s*([^;]+);/g)) {
      const raw = d[2].trim();
      decls[d[1].toLowerCase()] = { raw, value: resolve(raw) };
    }
    blocks.push({ selector, decls });
  }
}
assert(blocks.length > 200, `expected to parse the stylesheet, got ${blocks.length} rules`);

const tinted = /^var\(--accent-(10|15|25|35)\)$/;
const offenders = [];
for (const { selector, decls } of blocks) {
  const color = decls.color;
  if (!color || color.raw !== 'var(--accent)') continue; // the #3b82f6 text token
  const bg = decls.background || decls['background-color'];
  if (!bg) continue;
  if (bg.raw === 'var(--bg-tertiary)') {
    offenders.push(`${selector} — color: var(--accent) on background: var(--bg-tertiary)`);
  } else if (tinted.test(bg.raw)) {
    offenders.push(`${selector} — color: var(--accent) on background: ${bg.raw}`);
  }
}
assert.strictEqual(
  offenders.length,
  0,
  [
    'WCAG AA #67 regression: --accent (#3b82f6) is 3.98:1 on --bg-tertiary and',
    '3.29:1 on the --accent-15 count badge, under the 4.5:1 floor for body text.',
    'Use --accent-fg for text on a tinted dark surface; keep --accent for',
    'borders/fills, where the text-contrast rule does not apply.',
    '',
    ...offenders,
  ].join('\n')
);

// ---- the numbers behind the invariant ----------------------------------
const accent = parseColor(tokens['--accent']);
const accentFg = parseColor(tokens['--accent-fg']);
const tertiary = parseColor(tokens['--bg-tertiary']);
const tint = parseColor(resolve(tokens['--accent-15']));
const onTint = over(tint, tertiary);

const check = (label, got, need) =>
  assert(got >= need, `${label}: ${got.toFixed(2)}:1 < ${need}:1 required`);

check('accent-fg on bg-tertiary', ratio(accentFg, tertiary), 4.5);
check('accent-fg on the accent-15 count badge', ratio(accentFg, onTint), 4.5);
// The two tokens must differ for the rule above to mean anything: --accent is
// deliberately kept as the darker border/fill colour.
assert(
  ratio(accent, tertiary) < 4.5,
  `--accent is now ${ratio(accent, tertiary).toFixed(2)}:1 on --bg-tertiary; if it passes, ` +
    'this test no longer documents why text needs --accent-fg'
);
assert(lum(accentFg) > lum(accent), '--accent-fg must be lighter than --accent');

console.log(
  `a11y-contrast: --accent on --bg-tertiary ${ratio(accent, tertiary).toFixed(2)}:1 (border/fill only), ` +
    `--accent-fg ${ratio(accentFg, tertiary).toFixed(2)}:1 (text) — ${blocks.length} rules scanned, 0 offenders`
);
