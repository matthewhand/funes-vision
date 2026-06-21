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

console.log('timelineEmptyState: all assertions passed');
