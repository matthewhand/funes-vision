// Node assert test for the pure visitAltText() helper in index.html — a
// human, screen-reader-friendly alt for a Timeline visit thumbnail (it used
// to be alt="", announced as an unlabelled image). Run: node tests/test_visit_alt.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:visitAltText ===\n([\s\S]*?)\n\s*\/\/ === \/pure:visitAltText/);
assert(m, 'visitAltText sentinel block not found in index.html');
eval(m[1]); // defines visitAltText

assert.strictEqual(visitAltText('person', '10:39 AM', 5, false), 'person visit, 10:39 AM, 5 frames');
assert.strictEqual(visitAltText('cat', '2:14 PM', 1, false), 'cat visit, 2:14 PM');       // singular -> no frame count
assert.strictEqual(visitAltText('person', '10:39 AM', 3, true), 'person visit, 10:39 AM, 3 frames (ongoing)');
assert.strictEqual(visitAltText('dog', '9:00 AM', 2, false), 'dog visit, 9:00 AM, 2 frames');
// Defensive: missing label / time -> never an empty or "undefined" alt.
assert.strictEqual(visitAltText('', null, 0, false), 'object visit');
assert.strictEqual(visitAltText(undefined, undefined, 1, false), 'object visit');

console.log('visitAltText: all assertions passed');
