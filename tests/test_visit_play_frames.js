// Visit flipbook must not borrow another camera or an hours-earlier scene.
// Run: node tests/test_visit_play_frames.js
const fs = require('fs');
const assert = require('assert');
const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:visitPlayFrames ===\n([\s\S]*?)\n\s*\/\/ === \/pure:visitPlayFrames/);
assert(m, 'visitPlayFrames sentinel not found');
eval(m[1]);

const t = (hh, mm) => new Date(2026, 5, 18, hh, mm, 0);
const item = (f, ip, dateObj) => ({ f, meta: { ip, dateObj } });

const items = [
  item('empty.jpg', '10.0.0.21', t(7, 2)),
  item('person1.jpg', '10.0.0.21', t(10, 14)),
  item('person2.jpg', '10.0.0.21', t(10, 15)),
  item('bird.jpg', '10.0.0.22', t(12, 0)),
  item('dog1.jpg', '10.0.0.22', t(14, 30)),
  item('dog2.jpg', '10.0.0.22', t(14, 30)),
];

// Dog visit is indices 4–5. Lead-in must NOT include the 10am person or the
// 12pm bird on a different (or same) camera outside the 5-minute window.
assert.deepStrictEqual(visitPlayFrames(items, 4, 5), ['dog1.jpg', 'dog2.jpg']);

// Same camera, 2 minutes earlier → included as context.
const close = [
  item('approach.jpg', '10.0.0.21', t(10, 13)),
  item('person1.jpg', '10.0.0.21', t(10, 14)),
  item('person2.jpg', '10.0.0.21', t(10, 15)),
];
assert.deepStrictEqual(visitPlayFrames(close, 1, 2), [
  'approach.jpg', 'person1.jpg', 'person2.jpg',
]);

// Same camera, 20 minutes earlier → excluded.
const far = [
  item('morning.jpg', '10.0.0.21', t(9, 50)),
  item('person1.jpg', '10.0.0.21', t(10, 14)),
];
assert.deepStrictEqual(visitPlayFrames(far, 1, 1), ['person1.jpg']);

assert.deepStrictEqual(visitPlayFrames([], 0, 0), []);
assert.deepStrictEqual(visitPlayFrames(items, 5, 1), []);

console.log('visitPlayFrames: all assertions passed');
