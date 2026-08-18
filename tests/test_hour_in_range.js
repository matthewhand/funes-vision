// Node assert test for the pure hourInRange() helper in index.html —
// inclusive hour membership, wrapping when start > end (Night 21–5).
// Also locks the grid predicate to that helper. Run: node tests/test_hour_in_range.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:hourInRange ===\n([\s\S]*?)\n\s*\/\/ === \/pure:hourInRange/);
assert(m, 'hourInRange sentinel block not found in index.html');
eval(m[1]); // defines hourInRange

// Contiguous (morning 6–11).
assert.strictEqual(hourInRange(6, 6, 11), true);
assert.strictEqual(hourInRange(11, 6, 11), true);
assert.strictEqual(hourInRange(7, 6, 11), true);
assert.strictEqual(hourInRange(5, 6, 11), false);
assert.strictEqual(hourInRange(12, 6, 11), false);

// All-day.
assert.strictEqual(hourInRange(0, 0, 23), true);
assert.strictEqual(hourInRange(23, 0, 23), true);
assert.strictEqual(hourInRange(12, 0, 23), true);

// Night wrap [21, 5]: 21–23 and 0–5. Hour 2 matches, noon does not, 22 does.
assert.strictEqual(hourInRange(21, 21, 5), true);
assert.strictEqual(hourInRange(22, 21, 5), true);
assert.strictEqual(hourInRange(23, 21, 5), true);
assert.strictEqual(hourInRange(0, 21, 5), true);
assert.strictEqual(hourInRange(2, 21, 5), true);
assert.strictEqual(hourInRange(5, 21, 5), true);
assert.strictEqual(hourInRange(6, 21, 5), false);
assert.strictEqual(hourInRange(12, 21, 5), false);
assert.strictEqual(hourInRange(20, 21, 5), false);

// Invalid hours (parseFilename fallback is -1) must not slip into a wrap.
assert.strictEqual(hourInRange(-1, 0, 23), false);
assert.strictEqual(hourInRange(-1, 21, 5), false);
assert.strictEqual(hourInRange(24, 0, 23), false);
assert.strictEqual(hourInRange(null, 0, 23), false);

// Grid must use the helper (not a second, non-wrapping compare).
assert.match(html, /if \(!hourInRange\(hr, state\.timeStart, state\.timeEnd\)\) return false;/);

console.log('hourInRange: all assertions passed');
