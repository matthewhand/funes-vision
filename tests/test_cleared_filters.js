// Node assert test for the pure clearedFilters() helper in index.html — the
// canonical "no filters" state used by the "Show all / Clear all filters"
// escape so it never dead-ends to an empty grid. Run: node tests/test_cleared_filters.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
function block(name) {
  const m = html.match(new RegExp('pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*\\/\\/ === \\/pure:' + name));
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
}
eval(block('clearedFilters'));

assert.deepStrictEqual(clearedFilters(), {
  searchQuery: '', activeFilter: 'all', objectFilters: [],
  activeDateFilter: 'all', timeStart: 0, timeEnd: 23, activeHourChartFilter: null,
});
// Returns a fresh object each call (no shared mutable refs).
const a = clearedFilters(), b = clearedFilters();
assert.notStrictEqual(a, b);
assert.notStrictEqual(a.objectFilters, b.objectFilters);

// It clears every dimension filterSnapshot can capture (no leftover keys that
// the reset would miss). Shape must match filterSnapshot's keys.
eval(block('filterSnapshot'));
const snapKeys = Object.keys(filterSnapshot({})).sort();
const clearKeys = Object.keys(clearedFilters()).sort();
assert.deepStrictEqual(clearKeys, snapKeys);

console.log('clearedFilters: all assertions passed');
