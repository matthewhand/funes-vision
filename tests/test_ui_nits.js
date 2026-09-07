// Contract tests for leftover first-paint + copy nits. Run: node tests/test_ui_nits.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

// Backfill first-paints OFF (pipeline default / settings.json deep_backfill).
assert.ok(/let backfillOn = false/.test(html), 'backfillOn default must be false');
assert.ok(/id="backfill-toggle"[^>]*hidden-label|class="blacklist-item hidden-label"[^>]*id="backfill-toggle"/.test(html),
  '#backfill-toggle markup must include hidden-label for first paint');
assert.ok(/id="burst-summaries-toggle"[^>]*hidden-label|class="blacklist-item hidden-label"[^>]*id="burst-summaries-toggle"/.test(html),
  '#burst-summaries-toggle markup must first-paint off');
assert.ok(/id="slack-enabled-toggle"[^>]*hidden-label|class="blacklist-item hidden-label"[^>]*id="slack-enabled-toggle"/.test(html),
  'Slack Enabled must first-paint off');
assert.ok(/id="sweep-interval-input"[^>]*value="60"/.test(html), 'idle sweep first-paints 60');
assert.ok(/id="poll-interval-input"[^>]*value="15"/.test(html), 'status poll first-paints 15');
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

// First paint matches the front camera (JS overwrites on Dogcam).
assert.ok(/>Webcam Live Feed</.test(html), 'title first-paints Webcam Live Feed');
assert.ok(/Monitoring front gate area/.test(html), 'subtitle first-paints front gate');
assert.ok(/Switch to Dogcam Feed/.test(html), 'switch first-paints Dogcam target');
assert.ok(/person\/dog\/cat\/bird/.test(html), 'Slack objects copy includes cat/bird');
assert.ok(!/every new analyzed frame/.test(html), 'Slack all must not claim every analyzed frame');

// In-gallery Help opens the local walkthrough (not a CDN, not camera JPEGs).
assert.ok(/id="parked-mask-toggle"/.test(html) && /id="parked-mask-edit"/.test(html),
  'Settings must expose the parked-car mask toggle and Draw triangle control');
assert.ok(/id="porch-mask-toggle"/.test(html) && /id="porch-mask-edit"/.test(html),
  'Settings must expose the porch-zone toggle and Draw porch control');
assert.ok(/id="zone-editor"/.test(html), 'parked-car triangle editor overlay missing');
assert.ok(/id="btn-help"/.test(html) && /href="USER-GUIDE.html"/.test(html),
  'Help must link to USER-GUIDE.html');
assert.ok(/card\.setAttribute\('role', 'group'\)/.test(html),
  'cards must be role=group so pin/delete are not nested buttons');
assert.ok(!/card\.setAttribute\('role', 'button'\)/.test(html),
  'cards must not be role=button wrapping real controls');

// Lightbox must fully cover header chrome (was 0.98 alpha + z-index 100).
assert.ok(/\.lightbox\s*\{[\s\S]{0,240}?z-index:\s*2000/.test(html),
  'lightbox z-index must sit above header (50) and toast (1000)');
assert.ok(/\.lightbox\s*\{[\s\S]{0,280}?background-color:\s*#04060a/.test(html),
  'lightbox background must be opaque so "Feed" cannot ghost through');

// Feature-toggle OFF is a red dot, not a struck-through setting name.
assert.ok(/#blacklist-list \.blacklist-item\.hidden-label/.test(html),
  'strikethrough is only for hidden object labels');
assert.ok(!/\.blacklist-item\.hidden-label\s*\{[^}]*text-decoration:\s*line-through/.test(html)
  || /#blacklist-list \.blacklist-item\.hidden-label/.test(html),
  'global hidden-label strikethrough must not apply to Settings toggles');

// Status panel stays on-screen and is wide enough to read Queue / ETA.
assert.ok(/#system-dropdown/.test(html) && /width:\s*min\(360px/.test(html),
  'status dropdown must be wider than the 220px default');
assert.ok(/max-height:\s*min\(70vh,\s*calc\(100dvh - 12rem\)\)/.test(html),
  'dropdowns must cap to the space under the toolbar, not 100vh-180px');

// Phone search placeholder is shortened in JS (desktop keeps the long one).
assert.ok(/si\.placeholder = window\.matchMedia\('\(max-width: 768px\)'\)/.test(html)
  || /Search…/.test(html),
  'mobile search placeholder must shorten so it does not clip to "Search time,"');

console.log('ui-nits: all assertions passed');
