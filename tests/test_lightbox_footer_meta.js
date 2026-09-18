// Node assert test for the pure lightboxFooterMeta() helper in index.html —
// footer is caption/detected only (Channel / Event Type live in the heading).
// Run: node tests/test_lightbox_footer_meta.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:lightboxFooterMeta ===\n([\s\S]*?)\n\s*\/\/ === \/pure:lightboxFooterMeta/);
assert(m, 'lightboxFooterMeta sentinel block not found in index.html');
eval(m[1]); // defines lightboxFooterMeta

const meta = { channel: '01', eventType: 'Motion Detected', ip: '10.0.0.21',
               formattedDate: 'Jun 21, 2026', formattedTime: '06:08:26 pm' };

const out = lightboxFooterMeta(meta, ' | AI: No candidates');
assert.strictEqual(out, 'AI: No candidates');
assert.ok(!/Channel|Event Type|Created|Jun 21|06:08:26/.test(out),
  'footer must not repeat channel, event type, or heading date/time');

assert.strictEqual(lightboxFooterMeta(meta, ''), '');
assert.strictEqual(lightboxFooterMeta(meta, ' | Detected: Person'), 'Detected: Person');

assert.strictEqual(lightboxFooterMeta({}, ' | AI: Scanned (Clear)'), 'AI: Scanned (Clear)');
assert.strictEqual(lightboxFooterMeta(null, ''), '');

console.log('lightboxFooterMeta: all assertions passed');
