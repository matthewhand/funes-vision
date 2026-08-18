// Contract tests for leftover first-paint + copy nits. Run: node tests/test_ui_nits.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

// Backfill first-paints OFF (pipeline default / settings.json deep_backfill).
assert.ok(/let backfillOn = false/.test(html), 'backfillOn default must be false');
assert.ok(/id="backfill-toggle"[^>]*hidden-label|class="blacklist-item hidden-label"[^>]*id="backfill-toggle"/.test(html),
  '#backfill-toggle markup must include hidden-label for first paint');
assert.ok(/When idle, verify leftover empties and car-only skips \(near detections first\)/.test(html),
  'backfill title/toast must describe leftover empties, not ALL images newest first');
assert.ok(!/ALL images, newest first/i.test(html),
  'must not claim backfill walks ALL images newest first');

// Object-filter chip + settings + lightbox + status copy.
assert.ok(/allBtn\.textContent = 'All objects'/.test(html), 'chip must be All objects');
assert.ok(!/All AI/.test(html), 'All AI copy must be gone');
assert.ok(/Vision deep passes/.test(html), 'settings label Vision deep passes');
assert.ok(/>Idle backfill</.test(html), 'settings label Idle backfill');
assert.ok(/Detected: \$\{found\.join/.test(html), 'lightbox Detected: prefix');
assert.ok(!/AI DETECTED/.test(html), 'AI DETECTED copy must be gone');
assert.ok(!/<strong>AI:<\/strong>/.test(html), 'lightbox caption must drop AI: prefix');
assert.ok(/>This view</.test(html), 'status heading This view');
assert.ok(!/>Insights</.test(html), 'Insights heading must be gone');
assert.ok(/s\.llm\.allow_cloud \? ' \(\+cloud\)' : ''/.test(html),
  '(+cloud) only when allow_cloud');
assert.ok(/s\.llm\.allow_cloud \? ` \/ \$\{s\.metrics\.cloud\} cloud` : ''/.test(html),
  'cloud counts hidden unless allow_cloud');

// Lightbox alt uses the same card labels.
assert.ok(/img\.alt = cardAriaLabel\(meta, lbLabels\)/.test(html),
  'lightbox alt must use cardAriaLabel on every open/navigate');

// GET /api/integrations 409 must not look like "not configured".
const loadInt = html.match(/async function loadIntegrations\(\) \{([\s\S]*?)\n      \}/);
assert(loadInt, 'loadIntegrations not found');
assert.ok(/includes\('409'\)/.test(loadInt[1]), 'loadIntegrations must special-case 409');
assert.ok(/integrations\.json unreadable/.test(loadInt[1]),
  '409 status must say integrations.json unreadable');
assert.ok(!/409[\s\S]{0,200}not configured/.test(loadInt[1]),
  '409 path must not blank tokens as not configured');

// Filters popover shares the mobile sheet treatment.
assert.ok(/\.blacklist-dropdown,\s*#filters-dropdown/.test(html),
  '#filters-dropdown must share the mobile blacklist-dropdown sheet rules');

// In-gallery Help opens the local walkthrough (not a CDN, not camera JPEGs).
assert.ok(/id="btn-help"/.test(html) && /href="USER-GUIDE.html"/.test(html),
  'Help must link to USER-GUIDE.html');
assert.ok(/card\.setAttribute\('role', 'group'\)/.test(html),
  'cards must be role=group so pin/delete are not nested buttons');
assert.ok(!/card\.setAttribute\('role', 'button'\)/.test(html),
  'cards must not be role=button wrapping real controls');

console.log('ui-nits: all assertions passed');
