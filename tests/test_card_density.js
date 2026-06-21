// Contract test: card actions reveal on hover/focus (declutter), and the
// verified-detection tags use the calm tinted recipe (not loud solid fills).
// Run: node tests/test_card_density.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

// 1. .card-actions hidden by default, revealed on hover/focus-within
const ca = html.match(/\.card-actions\s*\{([\s\S]*?)\}/);
assert(ca, '.card-actions rule not found');
assert(/opacity:\s*0/.test(ca[1]), '.card-actions should start opacity:0 (hidden until hover/focus)');
assert(/\.image-card:hover\s+\.card-actions/.test(html), 'hover should reveal .card-actions');
assert(/\.image-card:focus-within\s+\.card-actions/.test(html), 'keyboard focus-within should reveal .card-actions');
assert(/@media\s*\(hover:\s*none\)/.test(html), 'touch/no-hover devices must always show the actions');
assert(/\.image-grid\.list\s+\.card-actions\s*\{[^}]*opacity:\s*1/.test(html),
  'list view should show row actions always (not hover-only)');

// 2. verified tags use tinted rgba backgrounds, not solid hex fills
for (const tag of ['tag-person', 'tag-dog', 'tag-cat', 'tag-face', 'tag-body']) {
  const m = html.match(new RegExp('\\.' + tag + '\\s*\\{([^}]*)\\}'));
  assert(m, '.' + tag + ' rule not found');
  assert(/background:\s*rgba\(/.test(m[1]), '.' + tag + ' should use a tinted rgba background (calm recipe): ' + m[1]);
  assert(/border:/.test(m[1]), '.' + tag + ' should have a border (consistent with disputed/potential): ' + m[1]);
}

console.log('card-density: actions hover/focus-reveal + tinted tag recipe verified');
