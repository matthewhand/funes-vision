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
  searchQuery: '', objectFilters: [],
  activeDateFilter: 'all', timeStart: 0, timeEnd: 23, activeHourChartFilter: null,
});
// Returns a fresh object each call (no shared mutable refs).
const a = clearedFilters(), b = clearedFilters();
assert.notStrictEqual(a, b);
assert.notStrictEqual(a.objectFilters, b.objectFilters);

// View tab is not a filter: "Clear all" must leave Timeline/Objects/All alone.
assert.ok(!Object.prototype.hasOwnProperty.call(clearedFilters(), 'activeFilter'),
  'clearedFilters must omit activeFilter so reset cannot switch the tab');

// Every cleared dimension is a filterSnapshot field (no leftover keys the
// reset would miss). Snapshot still records the tab for saved searches.
eval(block('filterSnapshot'));
const snapKeys = Object.keys(filterSnapshot({})).sort();
const clearKeys = Object.keys(clearedFilters()).sort();
assert.ok(!clearKeys.includes('activeFilter'));
assert.ok(clearKeys.every(k => snapKeys.includes(k)),
  'clearedFilters keys must be a subset of filterSnapshot');

// resetAllFilters must not assign the tab or force the All button selected.
const resetFn = html.match(/function resetAllFilters\(\) \{([\s\S]*?)\n    \}/);
assert(resetFn, 'resetAllFilters not found');
assert.ok(!/state\.activeFilter\s*=/.test(resetFn[1]),
  'resetAllFilters must leave state.activeFilter alone');
assert.ok(!/dataset\.filter === 'all'/.test(resetFn[1]),
  'resetAllFilters must not force the All tab selected');

console.log('clearedFilters: all assertions passed');
