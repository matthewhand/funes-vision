// Node assert test for the pure visitPassesTimeFilter() helper in index.html —
// Timeline visit vs time-of-day slider + hour-chart filter (same predicates
// as the grid). Run: node tests/test_visit_passes_time_filter.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:visitPassesTimeFilter ===\n([\s\S]*?)\n\s*\/\/ === \/pure:visitPassesTimeFilter/);
assert(m, 'visitPassesTimeFilter sentinel block not found in index.html');
eval(m[1]); // defines visitPassesTimeFilter

const visit = (startHour, endHour) => ({
  start: { meta: { hour: startHour } },
  end: { meta: { hour: endHour == null ? startHour : endHour } },
});

// All-day default: any valid start hour passes.
assert.strictEqual(visitPassesTimeFilter(visit(7), 0, 23, null), true);
assert.strictEqual(visitPassesTimeFilter(visit(0), 0, 23, null), true);
assert.strictEqual(visitPassesTimeFilter(visit(23), 0, 23, null), true);

// Morning 6–11: start-hour inside / outside.
assert.strictEqual(visitPassesTimeFilter(visit(7), 6, 11, null), true);
assert.strictEqual(visitPassesTimeFilter(visit(17), 6, 11, null), false);
assert.strictEqual(visitPassesTimeFilter(visit(6), 6, 11, null), true);
assert.strictEqual(visitPassesTimeFilter(visit(11), 6, 11, null), true);
assert.strictEqual(visitPassesTimeFilter(visit(5), 6, 11, null), false);
assert.strictEqual(visitPassesTimeFilter(visit(12), 6, 11, null), false);

// Overlap: visit 5–7 crosses into Morning.
assert.strictEqual(visitPassesTimeFilter(visit(5, 7), 6, 11, null), true);
assert.strictEqual(visitPassesTimeFilter(visit(4, 5), 6, 11, null), false);
assert.strictEqual(visitPassesTimeFilter(visit(11, 13), 6, 11, null), true);

// Hour-chart filter: must overlap that hour.
assert.strictEqual(visitPassesTimeFilter(visit(10, 12), 0, 23, 10), true);
assert.strictEqual(visitPassesTimeFilter(visit(10, 12), 0, 23, 11), true);
assert.strictEqual(visitPassesTimeFilter(visit(10, 12), 0, 23, 9), false);
assert.strictEqual(visitPassesTimeFilter(visit(7), 0, 23, 7), true);
assert.strictEqual(visitPassesTimeFilter(visit(7), 0, 23, 8), false);
// Midnight hour 0 is a real filter, not "unset".
assert.strictEqual(visitPassesTimeFilter(visit(0), 0, 23, 0), true);
assert.strictEqual(visitPassesTimeFilter(visit(1), 0, 23, 0), false);

// Combined: evening slider + hour 18.
assert.strictEqual(visitPassesTimeFilter(visit(17, 19), 17, 20, 18), true);
assert.strictEqual(visitPassesTimeFilter(visit(17, 19), 17, 20, 16), false);
assert.strictEqual(visitPassesTimeFilter(visit(10), 17, 20, 18), false);

// Midnight wrap: 23:00 → 01:00 overlaps night, not morning.
assert.strictEqual(visitPassesTimeFilter(visit(23, 1), 21, 23, null), true);
assert.strictEqual(visitPassesTimeFilter(visit(23, 1), 0, 2, null), true);
assert.strictEqual(visitPassesTimeFilter(visit(23, 1), 6, 11, null), false);
assert.strictEqual(visitPassesTimeFilter(visit(23, 1), 0, 23, 0), true);
assert.strictEqual(visitPassesTimeFilter(visit(23, 1), 0, 23, 12), false);

// Missing / unparseable hours (grid also rejects hour -1).
assert.strictEqual(visitPassesTimeFilter(null, 0, 23, null), false);
assert.strictEqual(visitPassesTimeFilter({}, 0, 23, null), false);
assert.strictEqual(visitPassesTimeFilter(visit(-1), 0, 23, null), false);
assert.strictEqual(visitPassesTimeFilter({ start: { meta: { hour: 7 } } }, 0, 23, null), true);

// renderEventsView must call the helper (not a second, divergent predicate).
assert.match(html, /visits = visits\.filter\(v => visitPassesTimeFilter\(/);

console.log('visitPassesTimeFilter: all assertions passed');
