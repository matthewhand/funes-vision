// Node assert test for the pure frameStatusBadge() helper in index.html — the
// calm "Clear" state shown for frames with no object badges, so negative rows
// don't render an empty middle. Run: node tests/test_frame_status_badge.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:frameStatusBadge ===\n([\s\S]*?)\n\s*\/\/ === \/pure:frameStatusBadge/);
assert(m, 'frameStatusBadge sentinel block not found in index.html');
eval(m[1]); // defines frameStatusBadge

// explicit negative (the common case) and scanned-no-object -> calm "Clear"
assert.deepStrictEqual(frameStatusBadge({ fast_pass: 'negative' }), { text: 'Clear', cls: 'badge-clear' });
assert.deepStrictEqual(frameStatusBadge({ _yolo: [] }), { text: 'Clear', cls: 'badge-clear' });
assert.deepStrictEqual(frameStatusBadge({ fast_pass: 'partial' }), { text: 'Clear', cls: 'badge-clear' });
// no analysis yet -> blank (don't invent a state)
assert.deepStrictEqual(frameStatusBadge(null), { text: '', cls: '' });
assert.deepStrictEqual(frameStatusBadge(undefined), { text: '', cls: '' });

console.log('frameStatusBadge: all assertions passed');
