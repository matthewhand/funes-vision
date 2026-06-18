// Node assert tests for the pure saved-search helpers in index.html:
// filterSnapshot, upsertSearch, removeSearch. Run: node tests/test_saved_searches.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
function block(name) {
  const m = html.match(new RegExp('pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*\\/\\/ === \\/pure:' + name));
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
}
eval(block('filterSnapshot'));
eval(block('upsertSearch'));
eval(block('removeSearch'));

// --- filterSnapshot: captures filter fields, Set -> sorted array, JSON-safe ---
const snap = filterSnapshot({
  searchQuery: 'gate', activeFilter: 'objects',
  objectFilters: new Set(['person', 'car']),
  activeDateFilter: '2026-06-18', timeStart: 6, timeEnd: 20, activeHourChartFilter: 7,
});
assert.deepStrictEqual(snap, {
  searchQuery: 'gate', activeFilter: 'objects', objectFilters: ['car', 'person'],
  activeDateFilter: '2026-06-18', timeStart: 6, timeEnd: 20, activeHourChartFilter: 7,
});
assert.strictEqual(JSON.stringify(JSON.parse(JSON.stringify(snap))), JSON.stringify(snap));
// Defaults on an empty/partial state.
const d = filterSnapshot({});
assert.deepStrictEqual(d, {
  searchQuery: '', activeFilter: 'events', objectFilters: [],
  activeDateFilter: 'all', timeStart: 0, timeEnd: 23, activeHourChartFilter: null,
});
assert.deepStrictEqual(filterSnapshot(null).objectFilters, []);

// --- upsertSearch: newest-first, dedup by name, capped ---
let list = upsertSearch([], 'People', { a: 1 });
list = upsertSearch(list, 'Cars', { a: 2 });
assert.deepStrictEqual(list.map(s => s.name), ['Cars', 'People']);   // newest first
list = upsertSearch(list, 'People', { a: 3 });                       // replace, moves to front
assert.deepStrictEqual(list.map(s => s.name), ['People', 'Cars']);
assert.deepStrictEqual(list[0].filters, { a: 3 });
assert.strictEqual(upsertSearch(list, '   ', {}).length, 2);          // blank name ignored
// Cap.
let big = [];
for (let i = 0; i < 15; i++) big = upsertSearch(big, 'n' + i, {}, 12);
assert.strictEqual(big.length, 12);
assert.strictEqual(big[0].name, 'n14');                              // newest kept

// --- removeSearch ---
assert.deepStrictEqual(removeSearch(list, 'People').map(s => s.name), ['Cars']);
assert.deepStrictEqual(removeSearch([], 'x'), []);
assert.deepStrictEqual(removeSearch(null, 'x'), []);

console.log('saved-searches: all assertions passed');
