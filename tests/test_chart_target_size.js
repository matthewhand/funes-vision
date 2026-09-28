// Regression test for #69: the activity-chart bars are interactive
// (role=button, tabindex=0, click-to-drill-down) and their painted width is
// only ~5px -- 24 hours share a 196px sidebar -- so the *target* has to be
// widened to the WCAG 2.5.8 floor of 24x24 CSS px without fattening the bar.
// A closed Motion panel used to leave its bars tabbable and the day bar
// measured 0x0.
//
// This is the static backstop (no browser in CI). The measured proof is a
// headless-browser run against tools/screenshots/proxy.py; see the PR.
// Run: node tests/test_chart_target_size.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

// WCAG 2.5.8 Target Size (Minimum), Level AA: 24x24 CSS px.
const MIN_TARGET = 24;

// Body of the first CSS rule whose selector list contains `selector`.
// Returns null when the rule is absent.
const cssRules = [];
{
  // index.html has exactly one <style> block, so a flat scan is enough: no
  // nesting in the sheet, and `{}` inside the rule body is not used.
  const style = html.match(/<style[^>]*>([\s\S]*?)<\/style>/);
  assert(style, 'expected a <style> block in index.html');
  // Comments carry a lot of the "why" for these rules, so strip them before
  // parsing or they get mistaken for part of the selector.
  const sheet = style[1].replace(/\/\*[\s\S]*?\*\//g, '');
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(sheet)) !== null) {
    cssRules.push({ selector: m[1].trim(), body: m[2] });
  }
}
function ruleBody(selector) {
  const hit = cssRules.find((r) => r.selector.split(',').some((s) => s.trim() === selector));
  return hit ? hit.body : null;
}

// Every px value a declaration assigns, so the assertions read the number out
// of the stylesheet instead of trusting a literal.
function pxValues(body, prop) {
  const out = [];
  const re = new RegExp('(?:^|;)\\s*' + prop + '\\s*:\\s*([^;]+)', 'g');
  let m;
  while ((m = re.exec(body)) !== null) {
    const nums = m[1].match(/\d+(\.\d+)?px/g) || [];
    nums.forEach((n) => out.push(parseFloat(n)));
  }
  return out;
}

// --- 1. the target element exists and is floored at 24px in both axes -------
const hit = ruleBody('.chart-hit');
assert(hit, 'expected a .chart-hit rule: the focusable target must not be the painted bar');
const hitMinW = pxValues(hit, 'min-width');
const hitMinH = pxValues(hit, 'min-height');
assert(hitMinW.length, '.chart-hit must set a min-width floor');
assert(hitMinH.length, '.chart-hit must set a min-height floor');
assert(Math.min(...hitMinW) >= MIN_TARGET,
  `.chart-hit min-width ${hitMinW}px is under the WCAG 2.5.8 ${MIN_TARGET}px floor`);
assert(Math.min(...hitMinH) >= MIN_TARGET,
  `.chart-hit min-height ${hitMinH}px is under the WCAG 2.5.8 ${MIN_TARGET}px floor`);
// `width: max(24px, 100%)` widens the target past its ~5px slot without
// shrinking a full-width (single-day) bar, so the floor has to survive it.
const hitWidth = /width\s*:\s*max\(\s*(\d+)px/.exec(hit);
assert(hitWidth, '.chart-hit should widen past its slot with width: max(<N>px, 100%)');
assert(parseFloat(hitWidth[1]) >= MIN_TARGET,
  `the width: max() floor is ${hitWidth[1]}px, under ${MIN_TARGET}px`);
// ...and it must be out of flow (absolute), or the bar would stretch to it.
assert(/position\s*:\s*absolute/.test(hit),
  '.chart-hit must be position: absolute so the painted bar keeps its ~5px width');
// Emitted after the bar, so it paints above it and owns the pointer.
assert(hit.indexOf('z-index') !== -1, '.chart-hit needs a z-index to sit above .chart-bar');

// --- 2. the wrapper itself can never be a zero-box focusable ---------------
const wrapper = ruleBody('.chart-bar-wrapper');
assert(wrapper, 'expected a .chart-bar-wrapper rule');
assert(Math.min(...pxValues(wrapper, 'min-height')) >= MIN_TARGET,
  '.chart-bar-wrapper must keep min-height >= 24px so a bar never collapses to 0 height');
assert(!/min-width\s*:\s*(2[5-9]|[3-9]\d|\d{3,})px/.test(wrapper),
  '.chart-bar-wrapper must NOT take a 24px min-width: 24 bars at 24px overflow the 196px sidebar');

// --- 3. both builders make the hit element the focusable control ------------
const hits = html.match(/className = 'chart-hit'/g) || [];
assert.strictEqual(hits.length, 2,
  'expected a .chart-hit target in BOTH chart builders (day bars + hour bars), found ' + hits.length);
['dayHit', 'hourHit'].forEach((v) => {
  assert(new RegExp("const " + v + " = document\\.createElement\\('div'\\)").test(html),
    `expected a ${v} element to be created for the chart target`);
  assert(new RegExp("bindActivatable\\(" + v + ",").test(html),
    `expected bindActivatable(${v}, ...) -- the target, not the wrapper, must be activatable`);
  assert(new RegExp(v + "\\.setAttribute\\('aria-label'").test(html),
    `expected ${v}.setAttribute('aria-label', ...) so the accessible name follows the target`);
  assert(new RegExp(v + "\\.setAttribute\\('aria-pressed'").test(html),
    `expected ${v}.setAttribute('aria-pressed', ...) so the toggle state is announced`);
});
// Regression guard: the focusable attributes must NOT go back on the wrapper.
assert(!/barWrapper\.setAttribute\('aria-(label|pressed)'/.test(html),
  'the chart wrapper must not carry the button semantics -- they belong on .chart-hit');
assert(!/bindActivatable\(\s*barWrapper\b/.test(html),
  'bindActivatable(barWrapper, ...) would make the ~5px wrapper the target again');
// The painted bar must keep its data-encoded size (no fat blocks).
const bar = ruleBody('.chart-bar');
assert(bar, 'expected a .chart-bar rule');
assert(/width\s*:\s*100%/.test(bar), '.chart-bar stays width: 100% of its flex slot (~5px)');
assert(!/min-width\s*:/.test(bar), '.chart-bar must not gain a min-width (that would fatten the data encoding)');

// --- 4. focus stays visibly indicated on the enlarged target ---------------
const focusRule = html.match(/\[role="button"\]:focus-visible[^{]*\{([^}]*)\}/);
assert(focusRule, 'expected the global [role="button"]:focus-visible rule');
assert(/outline\s*:\s*\d+px solid/.test(focusRule[1]),
  'the focus ring must stay an outline on the role="button" target');
assert(parseFloat(/outline\s*:\s*(\d+)px/.exec(focusRule[1])[1]) >= 2,
  'the focus ring must be at least 2px to stay visible on the enlarged target');

// --- 5. a closed Motion panel emits no bar targets at all ------------------
// (0x0 focus trap: Chrome skips layout inside a closed <details>, so the day
// bar measured 0x0 while the hour bars still measured 5x70.)
assert(/function clearActivityChart\(\)/.test(html),
  'expected clearActivityChart() to empty the chart containers');
assert(/if \(chartPanel && !chartPanel\.open\) return;/.test(html),
  'renderActivityChart() must bail out while the Motion panel is closed');
assert(/chartPanel\.addEventListener\('toggle'/.test(html),
  'the panel toggle must re-render (open) / clear (closed) the bars');
assert(/if \(chartPanel\.open\) renderActivityChart\(\);\s*else clearActivityChart\(\);/.test(html),
  'the toggle handler must render on open and clear on close');

console.log('chart-target-size: all assertions passed');
