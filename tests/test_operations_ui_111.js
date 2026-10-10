// UI #111: the camera-first context must be accessible and not replace
// the existing gallery controls, routes or mobile affordances.
const assert = require('assert');
const { src: html } = require('./helpers/load.cjs');

assert.match(html, /<section class="fv-context-bar" aria-labelledby="fv-context-title">/,
  'context card needs a named section');
assert.match(html, /<h2 id="fv-context-title">What happened on camera\?<\/h2>/,
  'context should communicate what this view does');
assert.match(html, /<a class="fv-context-link" href="\/cameras\/">/,
  'all-cameras action must point at the existing dashboard route');
assert.match(html, /href="#image-grid"/,
  'browse action must target the existing grid');
assert.match(html, /body\.mode-dashboard \.fv-context-bar\s*\{\s*display:\s*none;/,
  'gallery context must not appear on the camera dashboard');
assert.match(html, /\.fv-context-link:focus-visible\s*\{[^}]*outline:\s*2px/,
  'new interactive links must have visible keyboard focus');
assert.match(html, /@media \(max-width: 768px\)\s*\{[\s\S]*?\.fv-context-links\s*\{\s*width:\s*100%;\s*\}/,
  'context actions must wrap on mobile');
assert.match(html, /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.fv-context-link\s*\{\s*transition:\s*none;/,
  'new hover effects must respect reduced-motion preference');
for (const id of [
  'filter-tabs', 'stats-label', 'search-trigger', 'btn-switch-feed',
  'btn-system-status', 'live-toggle', 'date-list', 'image-grid',
  'dashboard-view', 'activity-chart',
]) assert.ok(html.includes('id="' + id + '"'), 'existing control removed: ' + id);
console.log('operations-ui-111: all assertions passed');
