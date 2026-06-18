// Node assert test for the pure formatSeconds() helper in index.html — the
// status-panel duration/age formatter ("Ns" / "Mm Ss"). Run:
//   node tests/test_format_seconds.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:formatSeconds ===\n([\s\S]*?)\n\s*\/\/ === \/pure:formatSeconds/);
assert(m, 'formatSeconds sentinel block not found in index.html');
eval(m[1]); // defines formatSeconds

// Sub-minute: whole seconds.
assert.strictEqual(formatSeconds(0), '0s');
assert.strictEqual(formatSeconds(30), '30s');
assert.strictEqual(formatSeconds(59.4), '59s');

// Minute+ as "Mm Ss".
assert.strictEqual(formatSeconds(60), '1m 0s');
assert.strictEqual(formatSeconds(90), '1m 30s');
assert.strictEqual(formatSeconds(125), '2m 5s');

// Rounding must NOT produce a 60-second component — it rolls into the minute.
assert.strictEqual(formatSeconds(119.6), '2m 0s');   // was "1m 60s"
assert.strictEqual(formatSeconds(59.6), '1m 0s');    // was "60s"
// No hours component (matches the original formatter): 3600 -> "60m 0s".
assert.strictEqual(formatSeconds(3599.6), '60m 0s');

console.log('formatSeconds: all assertions passed');
