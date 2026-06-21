// Node assert test for the pure showSearchClear() helper in index.html — the
// inline clear (×) shows only when the search field holds a non-blank query.
// Run: node tests/test_show_search_clear.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:showSearchClear ===\n([\s\S]*?)\n\s*\/\/ === \/pure:showSearchClear/);
assert(m, 'showSearchClear sentinel block not found in index.html');
eval(m[1]); // defines showSearchClear

assert.strictEqual(showSearchClear('person'), true);
assert.strictEqual(showSearchClear('a'), true);
assert.strictEqual(showSearchClear(''), false);
assert.strictEqual(showSearchClear('   '), false); // blank/whitespace -> no clear
assert.strictEqual(showSearchClear(null), false);
assert.strictEqual(showSearchClear(undefined), false);

console.log('showSearchClear: all assertions passed');
