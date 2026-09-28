// Contract: the status panel's disclosure rows and their muted text must meet
// WCAG 2.5.8 (24px target) and 1.4.3 (4.5:1 for body text). Issue #84.
//
// WHY THIS IS NOT JUST test_a11y_contrast.js: the old markup painted the
// schema descriptions with `color: var(--text-secondary)` AND an inline
// `opacity: 0.8`. The colour PAIR is 5.71:1, so the static test passed it --
// but a browser composites the alpha first and gets #7c8b9f on #1e293b, i.e.
// 4.21:1, below the floor. Only a composited computation (or a rule that
// refuses to put alpha on text) catches that class of defect, so this test
// pins the recipe: dim text is a solid token, and the alpha is gone.
//
// The two rows measured 334 x 19.67px -- under the 24px floor -- because they
// are painted as bare 0.7rem text. The floor is declared in CSS, not inline:
// widen the hit area, not the paint.
//
// Run: node tests/test_status_panel_a11y.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
// Only the <style> block: stripping /* */ across the whole document would eat
// past </style> (the inline script contains `/*` inside regex literals).
const styleBlocks = [...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)];
assert(styleBlocks.length >= 1, 'expected a <style> block in index.html');
const css = styleBlocks.map((m) => m[1]).join('\n').replace(/\/\*[\s\S]*?\*\//g, '');

// ---- token maths (WCAG 2.x relative luminance) --------------------------
const root = html.match(/:root\s*\{([\s\S]*?)\n\s*\}/);
assert(root, 'expected a :root token block');
const tokens = {};
for (const m of root[1].matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
  tokens[m[1]] = m[2].trim().replace(/\/\*[\s\S]*?\*\//g, '').trim();
}
for (const t of ['--text-secondary', '--text-dim', '--bg-tertiary']) {
  assert(tokens[t], `expected token ${t}`);
}
function parseColor(c) {
  c = c.trim();
  const m = c.match(/^#([0-9a-fA-F]{3,8})$/);
  assert(m, `expected a hex colour, got ${c}`);
  const h = m[1].length === 3 ? m[1].split('').map((x) => x + x).join('') : m[1];
  return {
    r: parseInt(h.slice(0, 2), 16),
    g: parseInt(h.slice(2, 4), 16),
    b: parseInt(h.slice(4, 6), 16),
    a: h.length === 8 ? parseInt(h.slice(6, 8), 16) / 255 : 1,
  };
}
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
const overAlpha = (fg, bg) => ({
  r: fg.r * fg.a + bg.r * (1 - fg.a),
  g: fg.g * fg.a + bg.g * (1 - fg.a),
  b: fg.b * fg.a + bg.b * (1 - fg.a),
  a: 1,
});

// ---- the dim token must clear 4.5:1 with no alpha left to fold ----------
const dim = parseColor(tokens['--text-dim']);
const secondary = parseColor(tokens['--text-secondary']);
const tertiary = parseColor(tokens['--bg-tertiary']);
const MIN = 4.5;

assert.strictEqual(dim.a, 1,
  '--text-dim must be a solid colour: alpha on body text is what composited ' +
  'this below 4.5:1 in the first place, and the static colour-pair scan cannot see it');
const dimRatio = ratio(dim, tertiary);
assert(dimRatio >= MIN,
  `--text-dim is ${dimRatio.toFixed(2)}:1 on --bg-tertiary, below the ${MIN}:1 floor`);
assert(lum(dim) < lum(secondary),
  '--text-dim must stay dimmer than --text-secondary, or the description text has ' +
  'no hierarchy left to the field name above it');
// The regression this replaced, computed so the threshold cannot be quietly
// relaxed: --text-secondary at the old inline opacity, composited.
const oldRatio = ratio(overAlpha({ ...secondary, a: 0.8 }, tertiary), tertiary);
assert(oldRatio < MIN,
  `--text-secondary at 0.8 now reads ${oldRatio.toFixed(2)}:1, so this test no longer ` +
  'documents the defect it was written for');

// ---- the alpha must be gone from the panel's markup ---------------------
// The schema property description: `<code>key</code> (type)<br><span>desc</span>`.
const descSpan = html.match(/<span style="margin-left:2px[^"]*">\$\{escapeHtml\(desc\)\}/);
assert(descSpan,
  'the schema property description span (margin-left:2px ... escapeHtml(desc)) is gone');
assert.ok(!/opacity/.test(descSpan[0]),
  'the description must not carry an inline opacity: composited over the ' +
  `--bg-tertiary schema block it measured 4.21:1 (${oldRatio.toFixed(2)}:1 computed here)`);
assert.ok(/color:\s*var\(--text-dim\)/.test(descSpan[0]),
  'the description must use the solid var(--text-dim) token');

// ---- no opacity on panel text anywhere ----------------------------------
// One pattern, not one selector: any inline style that sets a colour AND an
// opacity is a composited-contrast hazard. That is the #84 failure shape, and a
// static colour-pair scan cannot see it.
const opacityOnText = [...html.matchAll(/style="[^"]*"/g)]
  .map((m) => m[0])
  .filter((s) => /color:/.test(s) && /opacity:/.test(s));
assert.deepStrictEqual(opacityOnText, [],
  'no inline style may combine a colour with opacity -- the browser composites ' +
  'the alpha before the contrast maths runs:\n' + opacityOnText.join('\n'));

// ---- the 24px target floor (WCAG 2.5.8 AA) -----------------------------
function rule(selector) {
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    for (const sel of m[1].split(',')) {
      if (sel.trim().replace(/\s+/g, ' ') === selector) return m[2];
    }
  }
  return null;
}
const summaryRule = rule('#system-status-content > details > summary');
assert(summaryRule,
  'no #system-status-content > details > summary rule: the two disclosure rows ' +
  '("LLM Prompt & Schema", "AI Audit Trail") measured 334 x 19.67px, under the ' +
  '24px WCAG 2.5.8 (AA) floor. The panel is built from inline styles, so the floor ' +
  'has to be declared in CSS');
const minH = summaryRule.match(/min-height:\s*(\d+(?:\.\d+)?)px/);
assert(minH, 'the summary rule must set a min-height in px');
assert(parseFloat(minH[1]) >= 24,
  `summary min-height is ${minH[1]}px, below the 24px WCAG 2.5.8 (AA) floor`);

// The rows are still <summary> elements (real disclosure controls), and the
// panel still ships the two rows the measurement was taken from.
const summaries = [...html.matchAll(/<summary[^>]*>([^<]{0,80})/g)].map((m) => m[1].trim());
for (const label of ['LLM Prompt & Schema', 'AI Audit Trail']) {
  assert.ok(summaries.includes(label), `status panel must keep the "${label}" row`);
}

console.log(
  'status-panel-a11y: --text-dim ' + dimRatio.toFixed(2) + ':1 on --bg-tertiary ' +
  '(was ' + oldRatio.toFixed(2) + ':1 through inline opacity), summary rows ' + minH[1] + 'px'
);
