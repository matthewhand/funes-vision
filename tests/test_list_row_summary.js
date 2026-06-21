// Node assert test for the pure listRowSummary() helper in index.html — the
// dense scannable one-line summary / hover tooltip for a list-view row.
// Run: node tests/test_list_row_summary.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:listRowSummary ===\n([\s\S]*?)\n\s*\/\/ === \/pure:listRowSummary/);
assert(m, 'listRowSummary sentinel block not found in index.html');
eval(m[1]); // defines listRowSummary

const meta = { formattedDate: 'Jun 18, 2026', formattedTime: '05:55 am' };
assert.strictEqual(
  listRowSummary(meta, ['person', 'car'], 'two near the gate'),
  'Jun 18, 2026 · 05:55 am · person, car · “two near the gate”');
// no caption -> dropped, no trailing separator
assert.strictEqual(listRowSummary(meta, ['person'], ''),
  'Jun 18, 2026 · 05:55 am · person');
// no labels -> dropped
assert.strictEqual(listRowSummary(meta, [], 'a caption'),
  'Jun 18, 2026 · 05:55 am · “a caption”');
// only date/time
assert.strictEqual(listRowSummary(meta, [], ''), 'Jun 18, 2026 · 05:55 am');
// time only (no date)
assert.strictEqual(listRowSummary({ formattedTime: '05:55 am' }, ['dog'], ''),
  '05:55 am · dog');
// defensive: nullish meta/labels/caption -> '' (never throws / "undefined")
assert.strictEqual(listRowSummary(null, null, null), '');
assert.strictEqual(listRowSummary(undefined, undefined, undefined), '');
assert.strictEqual(listRowSummary({}, [], ''), '');

console.log('listRowSummary: all assertions passed');
