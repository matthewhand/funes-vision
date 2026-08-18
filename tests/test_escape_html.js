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

// Remaining model/user-string sinks must go through escapeHtml (labels, engine
// name, LLM tag, quiet-label list, chart titles, lightbox footer).
assert.ok(/text-transform: capitalize;">\$\{escapeHtml\(label\)\}<\/span>/.test(html),
  'blacklist menu labels must be escaped');
assert.ok(/insTop\.map\(t => `\$\{escapeHtml\(t\.label\)\}/.test(html),
  'insights "most seen" labels must be escaped');
assert.ok(/escapeHtml\(s\.llm\.model/.test(html),
  'status-panel LLM model tag must be escaped');
assert.ok(/gate_ignore_labels \|\| \[\]\)\.map\(l => escapeHtml\(l\)\)/.test(html),
  'quiet-label list must escape each label');
assert.ok(/title="\$\{escapeHtml\(title\)\}"/.test(html),
  'day-planner chart title (includes object-filter names) must be escaped');
assert.ok(/title="\$\{escapeHtml\(labelStr\)\}"/.test(html),
  'hour-chart title must be escaped');
assert.ok(/\$\{escapeHtml\(lightboxFooterMeta\(meta, aiText\)\)\}/.test(html),
  'lightbox footer (includes detected labels) must be escaped');

console.log('escapeHtml: all assertions passed');
