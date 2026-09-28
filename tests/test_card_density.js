// Contract test: card actions reveal on hover/focus (declutter), and the
// verified-detection tags use the calm tinted recipe (not loud solid fills).
// Run: node tests/test_card_density.js
const assert = require('assert');
const { src: html, rule } = require('./helpers/load.cjs');

// 1. .card-actions hidden by default, revealed on hover/focus-within
const ca = rule('.card-actions');
assert(/opacity:\s*0/.test(ca), '.card-actions should start opacity:0 (hidden until hover/focus)');
assert(/\.image-card:hover\s+\.card-actions/.test(html), 'hover should reveal .card-actions');
assert(/\.image-card:focus-within\s+\.card-actions/.test(html), 'keyboard focus-within should reveal .card-actions');
assert(/@media\s*\(hover:\s*none\)/.test(html), 'touch/no-hover devices must always show the actions');
assert(/\.image-grid\.list\s+\.card-actions\s*\{[^}]*opacity:\s*1/.test(html),
  'list view should show row actions always (not hover-only)');

// 2. verified tags use tinted rgba backgrounds, not solid hex fills
for (const tag of ['tag-person', 'tag-dog', 'tag-cat', 'tag-face', 'tag-body']) {
  const m = rule('.' + tag);
  assert(/background:\s*rgba\(/.test(m), '.' + tag + ' should use a tinted rgba background (calm recipe): ' + m);
  assert(/border:/.test(m), '.' + tag + ' should have a border (consistent with the potential tag): ' + m);
}

console.log('card-density: actions hover/focus-reveal + tinted tag recipe verified');
