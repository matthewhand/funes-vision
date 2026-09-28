// First-paint load path: catalogs must not block on cache-bust or a
// render-blocking head Lucide, and missing thumbs must not fetch full JPEGs.
// Run: node tests/test_load_path.js
const fs = require('fs');
const assert = require('assert');
const { src: html, head } = require('./helpers/load.cjs');

const createIndex = fs.readFileSync(__dirname + '/../create-index.sh', 'utf8');

assert.ok(!/images\.json\?t=/.test(html), 'must not cache-bust images.json with ?t=');
assert.ok(!/analysis\.json\?t=/.test(html), 'must not cache-bust analysis.json with ?t=');
assert.ok(/fetch(?:WithTimeout)?\('images\.json'/.test(html) && /fetch\('analysis\.json'/.test(html),
  'images.json and analysis.json must both be fetched');
assert.ok(/filenamesOnDate/.test(html),
  'Timeline must scope visit grouping to the selected day');
assert.ok(/cache:\s*'no-cache'/.test(html), 'catalog fetches must revalidate, not bypass cache');

assert.ok(!/<script[^>]+src="lucide\.min\.js"/.test(head()),
  'lucide.min.js must not be render-blocking in <head>');
assert.ok(/<script[^>]+src="lucide\.min\.js"/.test(html),
  'lucide.min.js must still load locally');

assert.ok(/_filenameMeta/.test(html), 'parseFilename must be memoized');

const observer = html.match(/const imageObserver = new IntersectionObserver\([\s\S]*?\}, \{ rootMargin/);
assert(observer, 'imageObserver missing');
assert.ok(!/img\.dataset\.full/.test(observer[0]),
  'missing thumb must not fall back to the full JPEG');
assert.ok(/IMG_UNAVAILABLE/.test(observer[0]),
  'missing thumb should use the unavailable glyph');

assert.ok(/slice_object_catalog/.test(createIndex),
  'create-index.sh must slice analysis.json to this camera');
assert.ok(/with_entries\(select/.test(createIndex),
  'catalog slice must keep only keys in images.json');

console.log('load-path: all assertions passed');
