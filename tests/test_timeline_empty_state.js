// Node assert test for the pure timelineEmptyState() helper in index.html — the
// Timeline (visits) empty view: accurate message + a "Clear all filters" escape
// when a filter/search is narrowing it, and a plain explanation otherwise (no
// reference to a specific, possibly-disabled model).
// Run: node tests/test_timeline_empty_state.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:timelineEmptyState ===\n([\s\S]*?)\n\s*\/\/ === \/pure:timelineEmptyState/);
assert(m, 'timelineEmptyState sentinel block not found in index.html');
eval(m[1]); // defines timelineEmptyState

// Filters active -> accurate "no match" message + offer the reset escape.
const filtered = timelineEmptyState(true);
assert.strictEqual(filtered.showReset, true);
assert.ok(/match the current filters/i.test(filtered.message), 'filtered msg should mention the filters');

// No filters -> explain what a visit is, offer no reset, and DON'T blame a model.
const empty = timelineEmptyState(false);
assert.strictEqual(empty.showReset, false);
assert.ok(/visit/i.test(empty.message), 'unfiltered msg should explain a visit');
assert.ok(!/gemma|verif/i.test(empty.message), 'must not reference a specific/disabled model');

// Hidden labels are settings, not a filter. Clear all does not unhide them, so
// renderEventsView must not OR state.blacklist into filtersActive (that would
// offer a dead "Clear all" while the empty grid stays). Unhide is the escape.
const ev = html.match(/function renderEventsView\([^)]*\) \{([\s\S]*?)\n    function openEventPlayer/);
assert(ev, 'renderEventsView not found');
const fa = ev[1].match(/const filtersActive = !!\(([\s\S]*?)\);/);
assert(fa, 'timeline filtersActive assignment missing');
assert.ok(!/blacklist/.test(fa[1]),
  'filtersActive must ignore blacklist — Clear all does not unhide labels');
assert.ok(/empty-unhide/.test(ev[1]), 'Unhide remains the escape for hidden labels');
assert.ok(/empty-reset/.test(ev[1]), 'Clear all stays available for real filters');

console.log('timelineEmptyState: all assertions passed');
