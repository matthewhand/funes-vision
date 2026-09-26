// Node assert test for the pure frameDurations() helper in index.html — the
// per-frame dwell times that make flipbook playback follow capture cadence.
// Run: node tests/test_frame_durations.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:frameDurations ===\n([\s\S]*?)\n\s*\/\/ === \/pure:frameDurations/);
assert(m, 'frameDurations sentinel block not found in index.html');
eval(m[1]); // defines frameDurations

const base = 1_700_000_000_000;

// The gap to the next frame drives its dwell; the last frame uses the default.
assert.deepStrictEqual(frameDurations([base, base + 500]), [500, 400]);
assert.deepStrictEqual(
  frameDurations([base, base + 1000, base + 1500]),
  [1000, 500, 400]
);

// Zero / negative / missing gaps fall back to the sane default (400ms).
assert.deepStrictEqual(frameDurations([base, base]), [400, 400]);
assert.deepStrictEqual(frameDurations([base, base - 5000]), [400, 400]);
assert.deepStrictEqual(frameDurations([NaN, NaN]), [400, 400]);
assert.deepStrictEqual(frameDurations([base, null]), [400, 400]);

// Clamp low: a positive sub-80ms gap floors to 80ms.
assert.deepStrictEqual(frameDurations([base, base + 10]), [80, 400]);

// Clamp high: an overnight gap caps at 2500ms.
assert.deepStrictEqual(
  frameDurations([base, base + 3 * 3600 * 1000]),
  [2500, 400]
);

// Accepts Date objects and ISO strings, not just epoch numbers.
assert.deepStrictEqual(
  frameDurations([new Date(base), new Date(base + 750).toISOString()]),
  [750, 400]
);

// Caller-supplied default/bounds are honored (and the default is clamped too).
assert.deepStrictEqual(
  frameDurations([base, base + 40], { defaultMs: 1000, minMs: 50, maxMs: 900 }),
  [50, 900]
);

// Length always matches the input; non-arrays yield [].
assert.strictEqual(frameDurations([]).length, 0);
assert.strictEqual(frameDurations([base]).length, 1);
assert.deepStrictEqual(frameDurations(null), []);
assert.deepStrictEqual(frameDurations('nope'), []);

console.log('frameDurations: all assertions passed');
