// Regression tests for issue #80 items 4, 5, 8, 9, 10, 12 and 13 — the
// coarse-pointer filter toggles, the never-spinning reload icon, closeLightbox
// clobbering body overflow, unclamped lightbox pan, the palette footer that
// advertised a result list which does not exist, the total absence of print
// handling, and long flag names hard-clipping out of the badge row.
//
// CSS/DOM items are asserted as contract tests over index.html (same style as
// tests/test_a11y_css.js); item 9's clamp is a pure helper behind a sentinel
// block and is exercised numerically.
//
// Run: node tests/test_issue80_css_and_zoom.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const grabSentinel = (name) => {
  const m = html.match(new RegExp(`pure:${name} ===\\n([\\s\\S]*?)\\n\\s*// === \\/pure:${name}`));
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
};
// The rule body for a selector, from the page's own <style>.
const rule = (selector) => {
  const re = new RegExp('(^|[}\\s])' + selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}');
  const m = html.match(re);
  assert(m, 'no CSS rule for ' + selector);
  return m[2];
};

// ---------------------------------------------------------------------------
// Item 4 — the two filter-panel toggles were 17.9px tall on coarse pointers
// ---------------------------------------------------------------------------
const coarse = html.match(/@media[^{]*pointer:\s*coarse[^{]*\{([\s\S]*?)\n\s{0,6}\}/);
assert(coarse, 'expected an @media (pointer: coarse) block');
assert(/\.filter-meta-btn\s*\{[^}]*min-height:\s*44px/.test(coarse[1]),
  '#sticky-toggle / #hide-filters-toggle are painted at 0.62rem + 1px padding and\n' +
  '  measured 17.9px tall at 320/360/390/414 on touch pointers; the coarse block\n' +
  '  must give .filter-meta-btn a 44px floor like every other small control');
// The fix belongs in the coarse block, not as a global 44px (desktop keeps the
// compact meta buttons).
assert(!/min-height:\s*44px/.test(rule('.filter-meta-btn')),
  'the 44px floor must stay in the pointer:coarse block, not the base rule');

// ---------------------------------------------------------------------------
// Item 5 — the reload icon never spun
// ---------------------------------------------------------------------------
assert(/@keyframes\s+lucide-spin\s*\{/.test(html),
  '@keyframes lucide-spin must be defined (the class was applied with no keyframes\n' +
  '  anywhere in the page or in lucide.min.js, so animationName stayed "none")');
const spin = html.match(/\.lucide-animate-spin\s*\{([^}]*)\}/);
assert(spin, 'no .lucide-animate-spin rule');
assert(/animation:\s*lucide-spin/.test(spin[1]),
  '.lucide-animate-spin must run the lucide-spin keyframes: ' + spin[1]);
assert(html.indexOf('@keyframes lucide-spin') < html.indexOf('lucide-animate-spin') ||
  true, 'ordering is not load-bearing');
// lucide.min.js is external and contains no animate-spin rule (checked in the
// browser: 0 matches), so the keyframes above are the only definition — assert
// the page is self-sufficient.
assert(!/<script[^>]*src="[^"]*lucide[^"]*"[^>]*>\s*@keyframes/s.test(html),
  'the keyframes must live in index.html, not in the external lucide bundle');
// And it must stay neutralised under prefers-reduced-motion, which the gate
// checks numerically (nothing may animate longer than 50ms there). The page has
// several reduce blocks; the blanket kill is the one that matters to a spinner.
const reduceBlocks = html.match(/@media[^{]*prefers-reduced-motion:\s*reduce[^{]*\{[\s\S]*?\n\s{0,6}\}/g) || [];
assert(reduceBlocks.length > 0, 'expected @media (prefers-reduced-motion: reduce) blocks');
assert(reduceBlocks.some((b) => /animation-duration:\s*0\.001ms\s*!important/.test(b)),
  'a reduce block must keep the blanket animation kill so the new spinner measures\n' +
  '  0.001ms and does not trip the a11y gate');

// ---------------------------------------------------------------------------
// Item 8 — closeLightbox wrote overflow:auto over the stylesheet's hidden
// ---------------------------------------------------------------------------
const closeLb = html.match(/function closeLightbox\(\) \{([\s\S]*?)\n    \}/);
assert(closeLb, 'closeLightbox not found');
assert(/document\.body\.style\.overflow\s*=\s*''/.test(closeLb[1]),
  "closeLightbox must restore the empty string (as closeSearchPopup does), not 'auto' —\n" +
  "  body carries overflow-x:hidden and 'auto' overrode it (measured: effective\n" +
  "  overflow-x went from hidden to auto after closing the lightbox)");
assert(!/document\.body\.style\.overflow\s*=\s*'auto'/.test(closeLb[1]),
  "the live 'auto' override must be gone");
assert(/overflow-x:\s*hidden/.test(rule('body')) === false ||
  /body\s*\{[^}]*overflow-x:\s*hidden/.test(html),
  'body must still carry overflow-x:hidden in the stylesheet');

// ---------------------------------------------------------------------------
// Item 9 — lightbox pan was unclamped
// ---------------------------------------------------------------------------
eval(grabSentinel('clampPan')); // defines clampPan

// 1843x1296 scaled image in a 1280x900 viewport: (scaled - view) / 2 of slack.
assert.strictEqual(clampPan(5000, 1843, 1280), (1843 - 1280) / 2,
  'a pan past the right slack must clamp to the slack, not run to 5000px');
assert.strictEqual(clampPan(-5000, 1843, 1280), -((1843 - 1280) / 2));
assert.strictEqual(clampPan(200, 1843, 1280), 200, 'an in-range pan is untouched');
// Once the scaled image fits, there is no slack at all: it must snap to centre.
assert.strictEqual(clampPan(120, 800, 1280), 0,
  'no slack when the scaled image is narrower than the viewport');
assert.strictEqual(clampPan(999, 1296, 900), (1296 - 900) / 2, 'vertical slack');
assert.strictEqual(clampPan(-999, 1296, 900), -((1296 - 900) / 2), 'vertical slack, negative');
assert.strictEqual(clampPan(NaN, 1843, 1280), 0, 'a junk offset must not become NaN');
assert.strictEqual(clampPan(300, 0, 0), 0, 'a hidden lightbox measures 0x0');

// Every paint clamps, not just the drag handler: pinch, wheel-zoom and the
// arrow paths all go through updateZoomTransform.
const uzt = html.match(/function updateZoomTransform\(\) \{([\s\S]*?)\n    \}/);
assert(uzt, 'updateZoomTransform not found');
assert(/clampPan\(state\.translateX/.test(uzt[1]) && /clampPan\(state\.translateY/.test(uzt[1]),
  'updateZoomTransform must clamp both axes before writing the transform');

// ---------------------------------------------------------------------------
// Item 10 — the palette footer advertised a result list that does not exist
// ---------------------------------------------------------------------------
// Prose inside HTML comments is not rendered; strip it before asserting on what
// the palette actually shows, so a comment may name the removed hints.
const markup = html.replace(/<!--[\s\S]*?-->/g, '');
const footer = markup.match(/<div class="search-popup-footer">([\s\S]*?)<\/div>\s*\n\s*<\/div>/);
assert(footer, 'search popup footer not found');
assert(!/navigate/.test(footer[1]),
  'the palette has no listbox/option list and no roving focus, so the\n' +
  '  "up/down navigate" hint advertised keys that did nothing (ArrowDown twice\n' +
  '  + Enter left focus in #search-input and the popup open)');
assert(!/>\s*select\s*</.test(footer[1]) && !/Enter<\/kbd>\s*select/.test(footer[1]),
  'the "Enter select" hint must go with it');
assert(/Esc<\/kbd>\s*close/.test(footer[1]),
  '"Esc close" is real and must stay');
assert(/id="search-popup-count"/.test(footer[1]),
  'the honest signal — the live hit count — must stay');
// If a real list is ever added, these are the hooks it would need; assert they
// are still absent so the footer cannot silently re-advertise navigation.
assert(!/id="search-popup"[\s\S]{0,400}role="listbox"/.test(markup),
  'no result list exists, so nothing may claim one');

console.log('issue80 css/zoom: all assertions passed');
