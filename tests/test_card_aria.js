// Node assert test for the pure cardAriaLabel() helper in index.html — the
// human accessible name for a (now keyboard-operable) gallery card.
// Run: node tests/test_card_aria.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:cardAriaLabel ===\n([\s\S]*?)\n\s*\/\/ === \/pure:cardAriaLabel/);
assert(m, 'cardAriaLabel sentinel block not found in index.html');
eval(m[1]); // defines cardAriaLabel

assert.strictEqual(
  cardAriaLabel({ formattedDate: 'Jun 18, 2026', formattedTime: '06:45:25 am' }, ['person', 'car']),
  'Jun 18, 2026 06:45:25 am — person, car');
assert.strictEqual(
  cardAriaLabel({ formattedDate: 'Jun 18', formattedTime: '06:45' }, []),
  'Jun 18 06:45 — no detections');
assert.strictEqual(cardAriaLabel({}, ['dog']), 'snapshot — dog');     // no date/time
assert.strictEqual(cardAriaLabel(null, null), 'snapshot — no detections');
assert.strictEqual(cardAriaLabel(undefined, undefined), 'snapshot — no detections');

console.log('cardAriaLabel: all assertions passed');
