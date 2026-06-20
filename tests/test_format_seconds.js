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
// An hour or more reads as "Hh Mm" (drops seconds) — no more "73m 12s".
assert.strictEqual(formatSeconds(3599), '59m 59s');   // just under an hour
assert.strictEqual(formatSeconds(3599.6), '1h 0m');   // rounds up to 3600
assert.strictEqual(formatSeconds(3600), '1h 0m');
assert.strictEqual(formatSeconds(3661), '1h 1m');     // 1h 1m 1s -> "1h 1m"
assert.strictEqual(formatSeconds(7325), '2h 2m');     // 2h 2m 5s
assert.strictEqual(formatSeconds(86400), '24h 0m');

console.log('formatSeconds: all assertions passed');
