// Node assert test for the pure formatDuration() helper in index.html — the
// human visit-length used on Timeline cards. Run: node tests/test_format_duration.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:formatDuration ===\n([\s\S]*?)\n\s*\/\/ === \/pure:formatDuration/);
assert(m, 'formatDuration sentinel block not found in index.html');
eval(m[1]); // defines formatDuration

const MIN = 60000, HR = 3600000;

// Single-frame or non-positive span => momentary.
assert.strictEqual(formatDuration(0, 1), 'momentary');
assert.strictEqual(formatDuration(5 * MIN, 1), 'momentary');   // count<=1 wins
assert.strictEqual(formatDuration(0, 5), 'momentary');         // zero span
assert.strictEqual(formatDuration(-MIN, 5), 'momentary');      // negative span guarded

// Multi-frame sub-minute visits read in seconds (not flattened to "momentary"
// nor rounded up to "1 min") — the accuracy fix.
assert.strictEqual(formatDuration(25 * 1000, 3), '25 sec');    // 25s, 3 frames
assert.strictEqual(formatDuration(50 * 1000, 3), '50 sec');    // was "1 min"
assert.strictEqual(formatDuration(1 * 1000, 2), '1 sec');      // brief 2-frame visit
assert.strictEqual(formatDuration(59 * 1000, 4), '59 sec');
assert.strictEqual(formatDuration(60 * 1000, 4), '1 min');     // exactly a minute

// Sub-hour spans in minutes (rounded).
assert.strictEqual(formatDuration(5 * MIN, 6), '5 min');
assert.strictEqual(formatDuration(90 * 1000, 3), '2 min');     // 90s rounds to 2 min
assert.strictEqual(formatDuration(59 * MIN, 10), '59 min');

// Hour+ spans as "Xh Ym".
assert.strictEqual(formatDuration(65 * MIN, 20), '1h 5m');
assert.strictEqual(formatDuration(90 * MIN, 20), '1h 30m');

// Whole-hour spans drop the " 0m" (the polish): "2h", not "2h 0m".
assert.strictEqual(formatDuration(60 * MIN, 20), '1h');
assert.strictEqual(formatDuration(2 * HR, 40), '2h');

console.log('formatDuration: all assertions passed');
