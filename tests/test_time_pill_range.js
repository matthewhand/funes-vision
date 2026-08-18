// Node assert test for the pure timePillRange() + timeRangeIsDefault() helpers
// in index.html (mobile time-of-day quick-pick). Run: node tests/test_time_pill_range.js
const fs = require('fs'); const assert = require('assert');
const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m1 = html.match(/pure:timePillRange ===\n([\s\S]*?)\n\s*\/\/ === \/pure:timePillRange/);
const m2 = html.match(/pure:timeRangeIsDefault ===\n([\s\S]*?)\n\s*\/\/ === \/pure:timeRangeIsDefault/);
assert(m1 && m2, 'sentinel blocks not found');
eval(m1[1]); eval(m2[1]);

assert.deepStrictEqual(timePillRange('all'), [0, 23]);
assert.deepStrictEqual(timePillRange('morning'), [6, 11]);
assert.deepStrictEqual(timePillRange('afternoon'), [12, 16]);
assert.deepStrictEqual(timePillRange('evening'), [17, 20]);
assert.deepStrictEqual(timePillRange('night'), [21, 5]);
assert.deepStrictEqual(timePillRange('bogus'), [0, 23]);   // fallback

assert.strictEqual(timeRangeIsDefault(0, 23), true);
assert.strictEqual(timeRangeIsDefault(6, 11), false);
assert.strictEqual(timeRangeIsDefault(0, 22), false);
assert.strictEqual(timeRangeIsDefault(1, 23), false);
console.log('timePillRange/timeRangeIsDefault: all assertions passed');
