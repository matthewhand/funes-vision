// Node assert test for the pure labelsMatchFilter() helper in index.html — the
// "match ANY" object-filter predicate used by the grid filter and activity
// chart. Run: node tests/test_labels_match_filter.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:labelsMatchFilter ===\n([\s\S]*?)\n\s*\/\/ === \/pure:labelsMatchFilter/);
assert(m, 'labelsMatchFilter sentinel block not found in index.html');
eval(m[1]); // defines labelsMatchFilter

const S = (...xs) => new Set(xs);

// Empty filter matches everything (incl. no labels).
assert.strictEqual(labelsMatchFilter(S('person', 'car'), S()), true);
assert.strictEqual(labelsMatchFilter(S(), S()), true);

// Single filter label: present => match, absent => no match.
assert.strictEqual(labelsMatchFilter(S('person', 'car'), S('person')), true);
assert.strictEqual(labelsMatchFilter(S('car'), S('person')), false);

// Multi-select is OR ("match ANY").
assert.strictEqual(labelsMatchFilter(S('cat'), S('dog', 'cat')), true);
assert.strictEqual(labelsMatchFilter(S('bird'), S('dog', 'cat')), false);

// No labels but an active filter => no match.
assert.strictEqual(labelsMatchFilter(S(), S('person')), false);

// Defensive: a missing/undefined filter set is treated as "no filter".
assert.strictEqual(labelsMatchFilter(S('person'), undefined), true);
assert.strictEqual(labelsMatchFilter(S('person'), null), true);

console.log('labelsMatchFilter: all assertions passed');
