// Node assert test for the pure isRecent() helper in index.html — gates a
// Timeline visit's "ongoing" flag on actual recency (so an old most-recent
// visit doesn't falsely read "ongoing"). Run: node tests/test_is_recent.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:isRecent ===\n([\s\S]*?)\n\s*\/\/ === \/pure:isRecent/);
assert(m, 'isRecent sentinel block not found in index.html');
eval(m[1]); // defines isRecent

// Within the window (epoch seconds).
assert.strictEqual(isRecent(1000, 1300, 600), true);   // age 300s
assert.strictEqual(isRecent(1000, 1000, 600), true);   // age 0
assert.strictEqual(isRecent(1000, 1600, 600), true);   // age exactly 600
// Outside the window.
assert.strictEqual(isRecent(1000, 1601, 600), false);  // age 601
assert.strictEqual(isRecent(1000, 2000, 600), false);
// Future timestamps are never "recent".
assert.strictEqual(isRecent(1000, 900, 600), false);   // age -100
// Default window is 600s.
assert.strictEqual(isRecent(1000, 1599), true);
assert.strictEqual(isRecent(1000, 1601), false);
// Bad input -> not recent.
assert.strictEqual(isRecent(NaN, 1000, 600), false);

console.log('isRecent: all assertions passed');
