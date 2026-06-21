// Node assert test for the pure lightboxTitle() helper in index.html — the clean
// human heading shown in the lightbox instead of the raw camera filename.
// Run: node tests/test_lightbox_title.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:lightboxTitle ===\n([\s\S]*?)\n\s*\/\/ === \/pure:lightboxTitle/);
assert(m, 'lightboxTitle sentinel block not found in index.html');
eval(m[1]); // defines lightboxTitle

// conforming frame -> "date · time"
assert.strictEqual(
  lightboxTitle({ formattedDate: 'Jun 21, 2026', formattedTime: '06:08:26 pm', original: 'x.jpg' }),
  'Jun 21, 2026 · 06:08:26 pm');
// partial: date only / time only
assert.strictEqual(lightboxTitle({ formattedDate: 'Jun 21, 2026', formattedTime: 'Unknown Time', original: 'x.jpg' }), 'Jun 21, 2026');
assert.strictEqual(lightboxTitle({ formattedDate: 'Unknown Date', formattedTime: '06:08:26 pm', original: 'x.jpg' }), '06:08:26 pm');
// non-conforming -> fall back to original filename (nothing lost)
assert.strictEqual(lightboxTitle({ formattedDate: 'Unknown Date', formattedTime: 'Unknown Time', original: 'odd_snapshot.png' }), 'odd_snapshot.png');
// missing/empty guards
assert.strictEqual(lightboxTitle(null), '');
assert.strictEqual(lightboxTitle({}), '');
assert.strictEqual(lightboxTitle({ original: 'only.jpg' }), 'only.jpg');

console.log('lightboxTitle: all assertions passed');
