// Node assert test for the pure dayLabel() helper embedded in index.html.
// Run: node tests/test_day_label.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:dayLabel ===\n([\s\S]*?)\n\s*\/\/ === \/pure:dayLabel/);
assert(m, 'dayLabel sentinel block not found in index.html');
eval(m[1]); // defines dayLabel

assert.strictEqual(dayLabel('2026-06-16', '2026-06-16'), 'Today');
assert.strictEqual(dayLabel('2026-06-15', '2026-06-16'), 'Yesterday');
assert.strictEqual(dayLabel('2026-06-14', '2026-06-16'), '');        // older
assert.strictEqual(dayLabel('2026-05-31', '2026-06-01'), 'Yesterday'); // month boundary
assert.strictEqual(dayLabel('2025-12-31', '2026-01-01'), 'Yesterday'); // year boundary
assert.strictEqual(dayLabel('2026-06-17', '2026-06-16'), '');        // future
assert.strictEqual(dayLabel('nope', '2026-06-16'), '');             // bad target
assert.strictEqual(dayLabel('2026-06-16', 'bad'), '');              // bad ref

const t = html.match(/pure:todayDateStr ===\n([\s\S]*?)\n\s*\/\/ === \/pure:todayDateStr/);
assert(t, 'todayDateStr sentinel missing');
eval(t[1]);
assert.strictEqual(todayDateStr(new Date('2026-08-21T14:00:00+10:00'), 'Australia/Sydney'), '2026-08-21');

const h = html.match(/pure:homeDateFilter ===\n([\s\S]*?)\n\s*\/\/ === \/pure:homeDateFilter/);
assert(h, 'homeDateFilter sentinel missing');
eval(h[1]);
assert.strictEqual(homeDateFilter('2026-08-21', ['2026-08-20', '2026-08-21']), '2026-08-21');
assert.strictEqual(homeDateFilter('2026-08-21', ['2026-06-18', '2026-08-20']), '2026-08-20');
assert.strictEqual(homeDateFilter('2026-08-21', []), '2026-08-21');

console.log('dayLabel: all assertions passed');
