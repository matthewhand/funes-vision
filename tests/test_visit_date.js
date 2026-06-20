// Node assert test for the pure visitDateLabel() helper in index.html — shows a
// single date, or "start – end" when a Timeline visit crosses midnight (so it
// isn't mislabeled to just the start date). Run: node tests/test_visit_date.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:visitDateLabel ===\n([\s\S]*?)\n\s*\/\/ === \/pure:visitDateLabel/);
assert(m, 'visitDateLabel sentinel block not found in index.html');
eval(m[1]); // defines visitDateLabel

assert.strictEqual(visitDateLabel('Jun 18, 2026', 'Jun 18, 2026'), 'Jun 18, 2026'); // same day
assert.strictEqual(visitDateLabel('Jun 18, 2026', 'Jun 19, 2026'),
  'Jun 18, 2026 – Jun 19, 2026');                                                   // crosses midnight
assert.strictEqual(visitDateLabel('Jun 18', undefined), 'Jun 18');                  // no end -> start only
assert.strictEqual(visitDateLabel('Jun 18', ''), 'Jun 18');
assert.strictEqual(visitDateLabel('Jun 18', 'Jun 18'), 'Jun 18');

console.log('visitDateLabel: all assertions passed');
