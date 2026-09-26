// Node assert tests for the pure Insights aggregation helpers in index.html:
// bucketByHour, labelCounts, busiestHour. Run: node tests/test_insights.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
function block(name) {
  const m = html.match(new RegExp('pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*\\/\\/ === \\/pure:' + name));
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
}
eval(block('bucketByHour'));
eval(block('labelCounts'));
eval(block('busiestHour'));
eval(block('taxonomy'));     // haTaxonomy() — canonical HA flag set
eval(block('labelStates'));   // computeLabelStates — HA flags must not count
eval(block('visibleLabels'));

// --- bucketByHour ---
let b = bucketByHour([0, 0, 13, 13, 13, 23]);
assert.strictEqual(b.length, 24);
assert.strictEqual(b[0], 2);
assert.strictEqual(b[13], 3);
assert.strictEqual(b[23], 1);
assert.strictEqual(b[5], 0);
// Out-of-range / unparseable (-1) and non-integers are ignored.
b = bucketByHour([-1, 24, 99, 3.5, null, undefined, 7]);
assert.strictEqual(b[7], 1);
assert.strictEqual(b.reduce((a, c) => a + c, 0), 1);
// Defensive: non-array -> all zeros.
assert.strictEqual(bucketByHour(null).reduce((a, c) => a + c, 0), 0);

// --- labelCounts (array of Sets/arrays of labels) ---
const sets = [new Set(['person', 'car']), new Set(['person']), ['car', 'car'], new Set()];
const lc = labelCounts(sets);
// 'car' = 3 (two in the array dupes count), 'person' = 2 -> sorted desc.
assert.deepStrictEqual(lc, [{ label: 'car', count: 3 }, { label: 'person', count: 2 }]);
// Ties break alphabetically.
assert.deepStrictEqual(labelCounts([new Set(['dog']), new Set(['cat'])]),
  [{ label: 'cat', count: 1 }, { label: 'dog', count: 1 }]);
assert.deepStrictEqual(labelCounts([]), []);
assert.deepStrictEqual(labelCounts(null), []);

// --- busiestHour ---
assert.strictEqual(busiestHour([0, 0, 5, 2, 5]), 2);   // first peak on tie
assert.strictEqual(busiestHour(bucketByHour([13, 13, 1])), 13);
assert.strictEqual(busiestHour(new Array(24).fill(0)), -1); // all zero
assert.strictEqual(busiestHour([]), -1);
assert.strictEqual(busiestHour(null), -1);

// Insights "most seen" is fed visibleLabels(); HA flags must not appear.
const mixedHA = { person: true, porch_access: true, postal_delivery: true, _yolo: ['person'], _llm: {} };
assert.deepStrictEqual(
  labelCounts([visibleLabels(mixedHA, {}, {})]),
  [{ label: 'person', count: 1 }]
);

console.log('insights: all assertions passed');
