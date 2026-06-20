// Node assert test for the pure tabWrap() helper in index.html — the focus-trap
// boundary logic for the lightbox/player dialogs. Run: node tests/test_tab_wrap.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:tabWrap ===\n([\s\S]*?)\n\s*\/\/ === \/pure:tabWrap/);
assert(m, 'tabWrap sentinel block not found in index.html');
eval(m[1]); // defines tabWrap

// Wrap at the boundaries.
assert.strictEqual(tabWrap(0, 3, true), 2);    // Shift+Tab off the first -> last
assert.strictEqual(tabWrap(2, 3, false), 0);   // Tab off the last -> first
// Interior moves are left to the browser (-1 = no wrap).
assert.strictEqual(tabWrap(1, 3, false), -1);
assert.strictEqual(tabWrap(1, 3, true), -1);
assert.strictEqual(tabWrap(0, 3, false), -1);  // forward off first = normal
assert.strictEqual(tabWrap(2, 3, true), -1);   // back off last = normal
// Single focusable wraps to itself (keeps focus inside).
assert.strictEqual(tabWrap(0, 1, false), 0);
assert.strictEqual(tabWrap(0, 1, true), 0);
// No focusables.
assert.strictEqual(tabWrap(0, 0, false), -1);
// Focus escaped the dialog (index -1) -> pull it back in.
assert.strictEqual(tabWrap(-1, 3, false), 0);  // forward -> first
assert.strictEqual(tabWrap(-1, 3, true), 2);   // backward -> last

console.log('tabWrap: all assertions passed');
