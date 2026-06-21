// Node assert test for the pure lightboxFooterMeta() helper in index.html — the
// lightbox technical-metadata line, with date+time intentionally omitted (they're
// in the heading via lightboxTitle, so showing them here too is redundant).
// Run: node tests/test_lightbox_footer_meta.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:lightboxFooterMeta ===\n([\s\S]*?)\n\s*\/\/ === \/pure:lightboxFooterMeta/);
assert(m, 'lightboxFooterMeta sentinel block not found in index.html');
eval(m[1]); // defines lightboxFooterMeta

const meta = { channel: '01', eventType: 'Motion Detected', ip: '10.0.0.21',
               formattedDate: 'Jun 21, 2026', formattedTime: '06:08:26 pm' };

// keeps the technical fields, drops date/time (no "Created"/date in the output)
const out = lightboxFooterMeta(meta, ' | AI: No candidates');
assert.strictEqual(out, 'Channel: 01 | Event Type: Motion Detected | Camera IP: 10.0.0.21 | AI: No candidates');
assert.ok(!/Created|Jun 21|06:08:26/.test(out), 'footer must not repeat the heading date/time');

// no aiText -> just the technical fields
assert.strictEqual(lightboxFooterMeta(meta, ''),
  'Channel: 01 | Event Type: Motion Detected | Camera IP: 10.0.0.21');

// aiText but no meta fields -> strip the leading separator (no " | AI: x")
assert.strictEqual(lightboxFooterMeta({}, ' | AI: Scanned (Clear)'), 'AI: Scanned (Clear)');
assert.strictEqual(lightboxFooterMeta(null, ''), '');

console.log('lightboxFooterMeta: all assertions passed');
