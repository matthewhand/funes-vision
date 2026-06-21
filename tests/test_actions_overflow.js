// Node assert test for the pure actionsOverflow() helper in index.html — how
// many toolbar actions don't fit, driving the compact (icon-only) collapse.
// Run: node tests/test_actions_overflow.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:actionsOverflow ===\n([\s\S]*?)\n\s*\/\/ === \/pure:actionsOverflow/);
assert(m, 'actionsOverflow sentinel block not found in index.html');
eval(m[1]); // defines actionsOverflow

assert.strictEqual(actionsOverflow(3, 400, 130), 0);  // 3 fit in 400 (3.07 -> 3)
assert.strictEqual(actionsOverflow(3, 300, 130), 1);  // only 2 fit
assert.strictEqual(actionsOverflow(3, 100, 130), 3);  // none fit
assert.strictEqual(actionsOverflow(5, 260, 130), 3);  // 2 fit, 3 overflow
assert.strictEqual(actionsOverflow(3, 390, 130), 0);  // exactly 3 fit
// Guards: bad/empty inputs -> 0 (never NaN/negative)
assert.strictEqual(actionsOverflow(0, 500, 130), 0);
assert.strictEqual(actionsOverflow(3, 500, 0), 0);
assert.strictEqual(actionsOverflow(3, -10, 130), 0);
assert.strictEqual(actionsOverflow(3, 500, -1), 0);

console.log('actionsOverflow: all assertions passed');
