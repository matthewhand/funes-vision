// P2: catalog-load failure state — an unreachable camera service must fail
// fast with a friendly "Camera service unreachable" empty state + stats-label,
// never an endless "Loading snapshots…". Run: node tests/test_load_error_state.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
function block(name) {
  const m = html.match(new RegExp('pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*// === /pure:' + name));
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
}
eval(block('loadErrorKind'));

// --- AbortError (fetch timeout) -> friendly unreachable state ---
let k = loadErrorKind({ name: 'AbortError', message: 'The operation was aborted.' });
assert.strictEqual(k.title, 'Camera service unreachable');
assert.match(k.message, /Couldn't reach the camera service/);

// --- 'timeout' by message ---
k = loadErrorKind(new Error('timeout'));
assert.strictEqual(k.title, 'Camera service unreachable');

// --- generic network failure -> catalog error ---
k = loadErrorKind(new Error('Network error loading images catalog'));
assert.strictEqual(k.title, 'Something went wrong');

// --- null/undefined -> generic ---
k = loadErrorKind(null);
assert.strictEqual(k.title, 'Something went wrong');

// --- images.json must load through a timeout-capable fetch ---
assert.match(html, /fetchWithTimeout\('images\.json'/, 
  'images.json load must go through fetchWithTimeout');
assert.match(html, /const LOAD_TIMEOUT_MS = 10000/, 'a catalog load timeout must exist');
assert.match(html, /AbortController\(\)/, 'timeout AbortController must exist');

// --- the catch must map failures via loadErrorKind and update the label ---
assert.match(html, /const k = loadErrorKind\(err\)/,
  'loadData catch must use loadErrorKind');
assert.match(html, /label\.textContent = k\.title/,
  'stats-label must reflect the failure state');

console.log('load-error-state: all assertions passed');