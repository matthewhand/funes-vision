// Node assert test for the pure smoothFlicker() helper in index.html — the
// single-frame debounce used by the Timeline visit grouping. Run:
//   node tests/test_smooth_flicker.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:smoothFlicker ===\n([\s\S]*?)\n\s*\/\/ === \/pure:smoothFlicker/);
assert(m, 'smoothFlicker sentinel block not found in index.html');
eval(m[1]); // defines smoothFlicker

const sf = (arr) => smoothFlicker(arr.slice());

// A lone frame whose neighbours agree is flipped to match them.
assert.deepStrictEqual(sf([true, false, true]), [true, true, true]);
assert.deepStrictEqual(sf([false, true, false]), [false, false, false]);

// Steady series are untouched.
assert.deepStrictEqual(sf([true, true, true]), [true, true, true]);
assert.deepStrictEqual(sf([false, false, false]), [false, false, false]);

// Endpoints have only one neighbour, so they are never smoothed.
assert.deepStrictEqual(sf([true, false]), [true, false]);
assert.deepStrictEqual(sf([false]), [false]);
assert.deepStrictEqual(sf([]), []);

// A genuine 2-frame gap is NOT smoothed (only single-frame flickers).
assert.deepStrictEqual(sf([true, false, false, true]), [true, false, false, true]);

// A single real visit surrounded by absence survives as its neighbours
// disagree with each other (T has F on both sides -> stays? no: F!=F false).
// [F,F,T,F,F]: i=2 T, prev F next F -> flip to F.
assert.deepStrictEqual(sf([false, false, true, false, false]), [false, false, false, false, false]);

// Alternating noise collapses under the forward single pass (documents
// the in-place behaviour relied on by computeVisits).
assert.deepStrictEqual(sf([false, true, false, true, false]), [false, false, false, false, false]);

// Returns the same array reference (in-place), as the caller depends on.
const a = [true, false, true];
assert.strictEqual(smoothFlicker(a), a);

console.log('smoothFlicker: all assertions passed');
