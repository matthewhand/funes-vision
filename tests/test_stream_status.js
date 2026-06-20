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

// Staleness: connected but no heartbeat ping within the window => stale.
assert.strictEqual(streamStatusText(true, 1000, 1010, 15).ok, true);   // age 10 < 15 -> live
assert.match(streamStatusText(true, 1000, 1010, 15).label, /live/i);
assert.strictEqual(streamStatusText(true, 1000, 1040, 15).ok, false);  // age 30 > 15 -> stale
assert.match(streamStatusText(true, 1000, 1040, 15).label, /stale/i);
assert.strictEqual(streamStatusText(true, 1000, 1000, 15).ok, true);   // age 0
// Backward-compatible: no ping data given => just live when connected.
assert.strictEqual(streamStatusText(true).ok, true);
// Not connected always wins over staleness.
assert.strictEqual(streamStatusText(false, 1000, 9999, 15).ok, false);

console.log('streamStatusText: all assertions passed');
