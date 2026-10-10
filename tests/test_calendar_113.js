// Date navigation contract: a bounded, accessible month calendar (#113).
const assert = require('assert');
const { src: html, grabFn } = require('./helpers/load.cjs');

const fn = grabFn('renderDateSidebar');
assert.match(fn, /new Set\(dates\.map\(d => d\.slice\(0, 7\)\)\)/, 'months derive from actual catalog dates');
assert.match(fn, /state\.calendarMonth/, 'browse month independently from active day');
assert.match(fn, /state\.calendarLastActiveDate !== selected/, 'reflect external date filter changes');
assert.match(fn, /createElement\('select'\)/, 'jump to recorded months');
assert.match(fn, /Previous recorded month/, 'previous-month navigation');
assert.match(fn, /Next recorded month/, 'next-month navigation');
assert.match(fn, /getUTCDay\(\) \+ 6\) % 7/, 'Monday-first alignment');
assert.match(fn, /new Date\(Date\.UTC\(year, month, 0\)\)\.getUTCDate\(\)/, 'month/year boundary');
assert.match(fn, /button\.disabled = !count/, 'empty dates not selectable');
assert.match(fn, /button\.setAttribute\('aria-label', fullName/, 'day includes full accessible date');
assert.match(fn, /button\.setAttribute\('aria-pressed'/, 'selection announced to screen readers');
assert.match(fn, /state\.activeDateFilter = dateStr/, 'use original date filter state');
assert.match(fn, /applyFiltersAndSearch\(\)/, 'render same gallery filters after clicking');
assert.doesNotMatch(fn, /sortedDates\.forEach/, 'no unbounded vertical date list');
assert.match(html, /id="date-list" aria-label="Camera archive calendar"/, 'labeled calendar region');
assert.match(html, /id="sidebar-toggle" aria-label="Show or hide calendar/, 'mobile discovery');
assert.match(html, /#date-list\.fv-calendar-host\s*\{/, 'old mobile strip replaced');
assert.match(html, /\.fv-cal-day:focus-visible/, 'keyboard focus visible');
assert.match(html, /@media \(max-width: 992px\)[\s\S]*?\.fv-calendar\s*\{\s*max-width:\s*420px/, 'mobile size bound');
console.log('calendar-113: all assertions passed');
