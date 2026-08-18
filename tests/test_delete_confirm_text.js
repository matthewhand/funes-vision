// deleteConfirmText: confirm delete with date/time + label, never the raw
// FTP filename (LAN IP + channel + MOTDEC). Run: node tests/test_delete_confirm_text.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:deleteConfirmText ===\n([\s\S]*?)\n\s*\/\/ === \/pure:deleteConfirmText/);
assert(m, 'deleteConfirmText sentinel missing');
eval(m[1]);

const meta = {
  formattedDate: '18 June 2026',
  formattedTime: '10:14:18 am',
  original: '10.0.0.21_01_20260618101418112_MOTDEC.jpg',
};

const person = deleteConfirmText(meta, 'person');
assert.ok(/18 June 2026/.test(person), 'must include the date');
assert.ok(/10:14:18 am/.test(person), 'must include the time');
assert.ok(/person/i.test(person), 'must include the label');
assert.ok(!/10\.0\.0\.21/.test(person), 'must not leak LAN IP');
assert.ok(!/MOTDEC/.test(person), 'must not leak FTP event token');
assert.ok(!/\.jpg/i.test(person), 'must not include the filename');

const noLabel = deleteConfirmText(meta, '');
assert.ok(/snapshot/.test(noLabel));
assert.ok(!/10\.0\.0\./.test(noLabel));

const unknown = deleteConfirmText(
  { formattedDate: 'Unknown Date', formattedTime: 'Unknown Time', original: '10.0.0.22_x.jpg' },
  null
);
assert.ok(!/10\.0\.0\./.test(unknown));
assert.ok(!/Unknown Date/.test(unknown));
assert.match(unknown, /snapshot/i);

assert.ok(!/confirm\(`Delete \$\{filename\}/.test(html),
  'deleteImage must not interpolate the raw filename into confirm()');

console.log('deleteConfirmText: all assertions passed');
