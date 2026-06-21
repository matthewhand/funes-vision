// Node assert test for the pure activeFiltersSummary() helper in index.html —
// the compact summary shown next to the collapsed "Filters" control.
// Run: node tests/test_active_filters_summary.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:activeFiltersSummary ===\n([\s\S]*?)\n\s*\/\/ === \/pure:activeFiltersSummary/);
assert(m, 'activeFiltersSummary sentinel block not found in index.html');
eval(m[1]); // defines activeFiltersSummary

const S = (...a) => new Set(a);
assert.deepStrictEqual(activeFiltersSummary(S(), 2), { text: '', count: 0 });
assert.deepStrictEqual(activeFiltersSummary(S('person'), 2), { text: 'person', count: 1 });
assert.deepStrictEqual(activeFiltersSummary(S('person', 'dog'), 2), { text: 'person, dog', count: 2 });
assert.deepStrictEqual(activeFiltersSummary(S('person', 'dog', 'cat'), 2), { text: 'person, dog +1', count: 3 });
assert.deepStrictEqual(activeFiltersSummary(S('a', 'b', 'c', 'd', 'e'), 2), { text: 'a, b +3', count: 5 });
// max defaults to 2 when omitted/invalid
assert.deepStrictEqual(activeFiltersSummary(S('person', 'dog', 'cat')), { text: 'person, dog +1', count: 3 });
assert.deepStrictEqual(activeFiltersSummary(S('person', 'dog', 'cat'), 0), { text: 'person, dog +1', count: 3 });
// max larger than count -> no "+N"
assert.deepStrictEqual(activeFiltersSummary(S('person', 'dog'), 5), { text: 'person, dog', count: 2 });
// nullish set -> empty
assert.deepStrictEqual(activeFiltersSummary(null, 2), { text: '', count: 0 });
assert.deepStrictEqual(activeFiltersSummary(undefined), { text: '', count: 0 });

console.log('activeFiltersSummary: all assertions passed');
