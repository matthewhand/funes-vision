// Node assert test for the pure escapeHtml() helper in index.html. Server/model
// strings (filenames, streamed Gemma captions, labels) are interpolated into
// innerHTML in the status panel + player; this prevents layout-break / XSS.
// Run: node tests/test_escape_html.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:escapeHtml ===\n([\s\S]*?)\n\s*\/\/ === \/pure:escapeHtml/);
assert(m, 'escapeHtml sentinel block not found in index.html');
eval(m[1]); // defines escapeHtml

assert.strictEqual(escapeHtml('<script>alert(1)</script>'),
  '&lt;script&gt;alert(1)&lt;/script&gt;');
assert.strictEqual(escapeHtml('a & b "c" \'d\''),
  'a &amp; b &quot;c&quot; &#39;d&#39;');
assert.strictEqual(escapeHtml('plain text'), 'plain text');
assert.strictEqual(escapeHtml(''), '');
assert.strictEqual(escapeHtml(null), '');
assert.strictEqual(escapeHtml(undefined), '');
assert.strictEqual(escapeHtml(42), '42');
// An attribute-breakout attempt is neutralised (quotes escaped).
assert.strictEqual(escapeHtml('" onerror="x'), '&quot; onerror=&quot;x');

console.log('escapeHtml: all assertions passed');
