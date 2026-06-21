// Node assert test for the pure timeRangeCollapsed() helper in index.html — the
// time-range row folds to just its header at the all-day default unless the user
// manually expanded it. Run: node tests/test_time_range_collapsed.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
function block(name) {
  const m = html.match(new RegExp(`pure:${name} ===\\n([\\s\\S]*?)\\n\\s*// === /pure:${name}`));
  assert(m, `${name} sentinel block not found in index.html`);
  return m[1];
}
eval(block('timeRangeIsDefault')); // defines timeRangeIsDefault
eval(block('timeRangeCollapsed')); // defines timeRangeCollapsed (uses the above)

// All-day default, not manually expanded -> collapsed.
assert.strictEqual(timeRangeCollapsed(0, 23, false), true);
assert.strictEqual(timeRangeCollapsed(0, 23, undefined), true);
// All-day default but manually expanded -> stays open.
assert.strictEqual(timeRangeCollapsed(0, 23, true), false);
// Narrowed range -> never collapses, regardless of the expand flag.
assert.strictEqual(timeRangeCollapsed(6, 18, false), false);
assert.strictEqual(timeRangeCollapsed(6, 18, true), false);
assert.strictEqual(timeRangeCollapsed(0, 22, false), false);
assert.strictEqual(timeRangeCollapsed(1, 23, false), false);

console.log('timeRangeCollapsed: all assertions passed');
