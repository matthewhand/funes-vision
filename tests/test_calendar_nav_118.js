// Multi-year calendar navigation contract (#118): the browse month is derived
// from the catalog, the arrows clamp at the ends of the recorded months, and an
// empty archive is still a navigable calendar rather than a blank rail.
const assert = require('assert');
const { grabFn } = require('./helpers/load.cjs');

const fn = grabFn('renderDateSidebar');
assert.match(fn, /new Set\(dates\.map\(d => d\.slice\(0, 7\)\)\)/, 'months derive from actual catalog dates');
assert.match(fn, /Object\.keys\(dateCounts\)\.sort\(\)/, 'dates (and so months) stay in chronological order');
assert.match(fn, /prev\.disabled = idx <= 0/, 'previous arrow disabled at the first recorded month');
assert.match(fn, /next\.disabled = idx < 0 \|\| idx >= months\.length - 1/, 'next arrow disabled at the last recorded month');
assert.match(fn, /option\.textContent = 'No recordings'/, 'empty archive keeps a labelled month option');
assert.match(fn, /choose\.disabled = true/, 'month select disabled when the archive is empty');
assert.match(fn, /new Date\(Date\.UTC\(year, month, 0\)\)\.getUTCDate\(\)/, 'leap-year aware month length');
assert.match(fn, /getUTCDay\(\) \+ 6\) % 7/, 'Monday-first week alignment');
assert.doesNotMatch(fn, /dates\.forEach/, 'no unbounded vertical date list');
console.log('calendar-nav-118: all assertions passed');
