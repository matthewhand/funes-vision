// Contract: the app must not overflow, slice text, or let one long label blow
// out the layout on a narrow phone. Issues #78.1 (320px page overflow), #78.2
// (visit rows hard-sliced below ~415px) and #78.3 (long saved-search names).
//
// These are static CSS assertions -- a backstop, not the proof. The proof is a
// headless-browser measurement at 320/330/360/390/414/1280 (see the PR's
// "Measured impact"); a DOM-as-text scan cannot see a layout, so it can only
// pin the recipe that produces one. Every assertion below fails on the code
// that shipped before the fix.
//
// WHY each rule, and not the tempting neighbour:
//   #78.1  .controls-wrapper is nowrap, #search-trigger has min-width:0 and
//          absorbs all the shrink, and #live-toggle (flex 0 1 auto, default
//          min-width:auto) cannot go below ~63px -- so at 320px scrollWidth
//          measured 330. The fix is the icon collapse the file already uses for
//          #switch-feed-label at 768px, not flex-wrap, which would reflow the
//          whole header.
//   #78.2  .image-grid clips with overflow-x:hidden, so a grid item wider than
//          the track is sliced with no ellipsis. The clip stays (it is what
//          keeps a wide card from scrolling the page); the item must fit
//          instead. min-width:0 belongs on the GRID ITEM .visit-log -- putting
//          it on .visit-row leaves the track floored at the nowrap min-content
//          of .visit-meta (measured 398px, unchanged).
//   #78.3  The saved-search name comes from an unbounded prompt(), so
//          .saved-chip can be asked to be wider than the 196px sidebar (or the
//          296px single-column sidebar at 320px) and then either spills into the
//          feed or -- if the name has no break opportunities -- pushes the whole
//          page sideways. max-width caps the pill, min-width:0 lets it shrink,
//          and the ellipsis on the label keeps the name on one readable line.
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

// Only the <style> block: stripping /* */ across the whole document would eat
// past </style> (the inline script contains `/*` inside regex literals).
const styleBlocks = [...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)];
assert.ok(styleBlocks.length >= 1, 'expected a <style> block in index.html');
const css = styleBlocks.map((m) => m[1]).join('\n').replace(/\/\*[\s\S]*?\*\//g, '');

function bodyOf(src, openIdx) {
  let depth = 0;
  for (let i = openIdx; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') {
      depth--;
      if (depth === 0) return src.slice(openIdx + 1, i);
    }
  }
  return '';
}

// Every `selector { body }` pair, keyed by exact selector (comma-separated
// selector lists are split, so `.a, .b` registers twice).
function styleRules() {
  const out = [];
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    for (const sel of m[1].split(',')) {
      const s = sel.trim().replace(/\s+/g, ' ');
      if (s) out.push({ selector: s, body: m[2] });
    }
  }
  return out;
}
const rules = styleRules();
const find = (selector) => rules.find((r) => r.selector === selector) || null;
const rule = (selector) => {
  const hit = find(selector);
  assert(hit, `no rule for selector "${selector}" in index.html`);
  return hit.body;
};
const decl = (body, prop) => {
  const m = body.match(new RegExp('(?:^|[;{\\s])' + prop + '\\s*:\\s*([^;]+);'));
  assert(m, `declaration "${prop}" not found (body: ${body.trim().slice(0, 120)})`);
  return m[1].trim();
};
// The body of the first `@media (max-width: N) { ... }` block that contains
// `selector` -- the media query a declaration is scoped to is the whole point.
function inMedia(query, selector) {
  const marker = '@media (max-width: ' + query + ')';
  let from = 0;
  while (true) {
    const idx = css.indexOf(marker, from);
    if (idx < 0) return null;
    const brace = css.indexOf('{', idx);
    const body = bodyOf(css, brace);
    if (body.includes(selector)) return body;
    from = idx + marker.length;
  }
}

// ---- #78.1 -- 320px horizontal overflow ---------------------------------
// The 320px repro: scrollWidth 330 vs clientWidth 320, with #live-toggle
// sitting at x 267->330, past the right edge.
const m360 = inMedia('360px', '#live-text');
assert(m360,
  'no @media (max-width: 360px) block collapsing #live-text: at 320px #live-toggle ' +
  'cannot shrink below ~63px, so documentElement.scrollWidth measured 330 against a ' +
  '320px clientWidth');
assert.strictEqual(decl(m360, 'display'), 'none',
  'the 360px block must set display: none on #live-text (icon-only Live pill)');
assert.ok(/id="live-text"/.test(html), 'the Live pill must keep its #live-text label');
assert.ok(/id="live-toggle"/.test(html), 'the Live switch must keep id="live-toggle"');
// Hiding the label must not hide the switch itself -- it is a role="switch"
// control, and the accessible name comes from aria-label, not the text node.
const liveToggleTag = html.match(/<div id="live-toggle"[\s\S]{0,300}?>/);
assert(liveToggleTag, '#live-toggle markup not found');
assert.ok(/role="switch"/.test(liveToggleTag[0]), '#live-toggle must stay a role="switch"');
assert.ok(/aria-label=/.test(liveToggleTag[0]),
  '#live-toggle must carry aria-label, so hiding #live-text leaves it named');
// The collapse must be bounded: at every wider breakpoint the label is back,
// because the overflow only exists below ~360px.
for (const q of ['480px', '768px', '960px']) {
  assert.strictEqual(inMedia(q, '#live-text'), null,
    `#live-text must not be hidden at ${q} -- the overflow only exists below ~360px`);
}
// Sanity: the nowrap controls row is what makes the shrink matter.
const controls = rule('.controls-wrapper');
assert.ok(/flex-wrap:\s*(nowrap|wrap)/.test(controls),
  '.controls-wrapper is the header row #live-toggle overflows');
const controls768 = inMedia('768px', '.controls-wrapper');
assert(controls768 && /flex-wrap:\s*nowrap/.test(controls768),
  'the 768px .controls-wrapper rule must stay nowrap: the 320px overflow came from ' +
  'that row, and wrapping it would reflow the whole header instead');

// ---- #78.2 -- visit rows hard-sliced below ~415px ------------------------
const log = rule('.visit-log');
assert.ok(/min-width:\s*0/.test(log),
  '.visit-log must set min-width: 0: as a grid item its default min-width:auto ' +
  'floors the track at the nowrap min-content of .visit-meta (measured 398px), and ' +
  '.image-grid then slices the row with overflow-x:hidden and no ellipsis');
assert.ok(/grid-column:\s*1\s*\/\s*-1/.test(log),
  '.visit-log must still span the full grid (grid-column: 1 / -1)');

// The falsified alternative, pinned so nobody re-derives it: min-width:0 on the
// ROW is not the same fix and leaves the track at 398px.
assert.ok(!/min-width:\s*0/.test(rule('.visit-row')),
  'min-width: 0 belongs on the grid item .visit-log, not .visit-row -- on the row ' +
  'it changes nothing (track stays 398px, 102px still sliced)');

// The clip itself is deliberate and must stay: the fix is that the item now
// fits inside it. Removing overflow-x:hidden would "fix" the symptom by
// letting the page scroll sideways, which is #78.1 all over again.
assert.ok(/overflow-x:\s*hidden/.test(rule('.image-grid')),
  '.image-grid must keep overflow-x: hidden');
const meta = rule('.visit-meta');
assert.ok(/white-space:\s*nowrap/.test(meta) && /text-overflow:\s*ellipsis/.test(meta),
  '.visit-meta stays one nowrap line that truncates with an ellipsis (it is the ' +
  'min-content floor, not the element width, that used to overflow the grid)');
const cap = rule('.visit-caption');
assert.ok(/white-space:\s*nowrap/.test(cap) && /text-overflow:\s*ellipsis/.test(cap),
  '.visit-caption truncates the same way');

// ---- #78.3 -- long saved-search names ------------------------------------
const chip = rule('.saved-chip');
assert.ok(/max-width:\s*100%/.test(chip),
  '.saved-chip must set max-width: 100%: the name is unbounded, so a chip can be ' +
  'wider than the list (measured 453px against a 296px list at 320px)');
assert.ok(/min-width:\s*0/.test(chip),
  '.saved-chip must set min-width: 0 so it may shrink below min-content');

const apply = rule('.saved-chip .saved-apply');
assert.ok(/min-width:\s*0/.test(apply),
  '.saved-apply must set min-width: 0: it is a flex item, so its default ' +
  'min-width:auto keeps the label at full width and the pill at 453px');
for (const [prop, why] of [
  ['overflow', 'to clip the overflowing label'],
  ['text-overflow', 'so the truncation is signalled, not a hard slice'],
  ['white-space', 'so the name stays on one line instead of wrapping the pill to 3-4 lines'],
]) {
  if (prop === 'text-overflow') {
    assert.ok(/text-overflow:\s*ellipsis/.test(apply), 'saved-apply: ' + why);
  } else if (prop === 'white-space') {
    assert.ok(/white-space:\s*nowrap/.test(apply), 'saved-apply: ' + why);
  } else {
    assert.ok(new RegExp('overflow:\\s*hidden').test(apply), 'saved-apply: ' + why);
  }
}
assert.ok(/@media\s*\(max-width:\s*768px\)[\s\S]*?\.saved-chip\s*\{[^}]*min-height:\s*36px/.test(css),
  'the coarse-pointer 36px chip floor must survive the rewrite (WCAG 2.5.8)');
// The unbounded input that motivates all of the above must stay unbounded here:
// clamping belongs in CSS, not in the prompt handler.
assert.ok(/prompt\('Name this view:'\)/.test(html),
  'the saved-search name still comes from prompt() -- the CSS is what bounds it');

console.log('narrow-mobile-layout: 320px overflow, visit-row clipping and long saved names are bounded');
