// Node assert test for the pure streamStatusText() helper in index.html — the
// SSE connected/degraded label shown in the status panel. Run:
//   node tests/test_stream_status.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:streamStatusText ===\n([\s\S]*?)\n\s*\/\/ === \/pure:streamStatusText/);
assert(m, 'streamStatusText sentinel block not found in index.html');
eval(m[1]); // defines streamStatusText

// Connected -> live/ok.
const live = streamStatusText(true);
assert.strictEqual(live.ok, true);
assert.match(live.label, /live/i);

// Not connected -> polling/degraded.
const degraded = streamStatusText(false);
assert.strictEqual(degraded.ok, false);
assert.match(degraded.label, /poll/i);

// Anything non-true is treated as not-connected (defensive: undefined/null).
assert.strictEqual(streamStatusText(undefined).ok, false);
assert.strictEqual(streamStatusText(null).ok, false);
assert.strictEqual(streamStatusText(0).ok, false);

console.log('streamStatusText: all assertions passed');
