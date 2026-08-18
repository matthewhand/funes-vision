// Node assert test for the pure matchesSearch() helper in index.html — the one
// search predicate used by BOTH the grid and the activity charts (previously
// the charts matched filename only, disagreeing with "Showing N of M").
// Run: node tests/test_matches_search.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:matchesSearch ===\n([\s\S]*?)\n\s*\/\/ === \/pure:matchesSearch/);
assert(m, 'matchesSearch sentinel block not found in index.html');
eval(m[1]); // defines matchesSearch

const F = { filename: '10.0.0.21_01_x_MOTDEC.jpg', formattedDate: 'Jun 18, 2026',
            formattedTime: '06:45:25 am', eventType: 'Motion Detected', caption: 'a person at the gate' };

assert.strictEqual(matchesSearch('', F), true);          // empty matches all
assert.strictEqual(matchesSearch(null, F), true);
assert.strictEqual(matchesSearch('person', F), true);    // caption
assert.strictEqual(matchesSearch('PERSON', F), true);    // case-insensitive
assert.strictEqual(matchesSearch('jun', F), true);       // formattedDate
assert.strictEqual(matchesSearch('06:45', F), true);     // formattedTime
assert.strictEqual(matchesSearch('motion', F), true);    // eventType
assert.strictEqual(matchesSearch('motdec', F), true);    // filename
assert.strictEqual(matchesSearch('zebra', F), false);    // no field matches
assert.strictEqual(matchesSearch('dog', { labels: ['person', 'dog'] }), true);
assert.strictEqual(matchesSearch('cat', { labels: ['person'] }), false);
// Missing/partial fields are safe.
assert.strictEqual(matchesSearch('x', {}), false);
assert.strictEqual(matchesSearch('x', { filename: 'x.jpg' }), true);
assert.strictEqual(matchesSearch('cap', { caption: null, filename: undefined }), false);

console.log('matchesSearch: all assertions passed');
