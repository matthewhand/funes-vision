// Node assert test for the pure visibleLabels()/showDisputedLabels() helpers
// in index.html. Disputed-hiding is a PRECISION-MODE feature: when deep passes
// are off we show every label from every inference pass.
// Depends on computeLabelStates (extracted too). Run: node tests/test_visible_labels.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
function block(name) {
  const m = html.match(new RegExp('pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*\\/\\/ === \\/pure:' + name));
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
}
eval(block('labelStates'));   // computeLabelStates
eval(block('visibleLabels')); // showDisputedLabels, visibleLabels

// --- showDisputedLabels(opts) ---
// Precision ON + show-unconfirmed OFF => hide disputed.
assert.strictEqual(showDisputedLabels({ precisionMode: true, showUnconfirmed: false }), false);
// Precision ON + show-unconfirmed ON => show disputed.
assert.strictEqual(showDisputedLabels({ precisionMode: true, showUnconfirmed: true }), true);
// Precision OFF => always show disputed (the new behaviour), regardless of toggle.
assert.strictEqual(showDisputedLabels({ precisionMode: false, showUnconfirmed: false }), true);
assert.strictEqual(showDisputedLabels({ precisionMode: false, showUnconfirmed: true }), true);
assert.strictEqual(showDisputedLabels(undefined), true); // no opts -> not precision -> show

// --- visibleLabels(analysis, aliases, opts) ---
const disputed = { person: true, _yolo: [], _llm: {} };          // Gemma-only claim => disputed
const verified = { person: true, _yolo: ['person'], _llm: {} };  // consensus => verified
const prelim   = { car: true, fast_pass: 'partial' };  // detector-only => preliminary

const vis = (a, o) => Array.from(visibleLabels(a, {}, o)).sort();

// Precision mode, unconfirmed off: disputed hidden, others shown.
assert.deepStrictEqual(vis(disputed, { precisionMode: true, showUnconfirmed: false }), []);
assert.deepStrictEqual(vis(verified, { precisionMode: true, showUnconfirmed: false }), ['person']);
assert.deepStrictEqual(vis(prelim,   { precisionMode: true, showUnconfirmed: false }), ['car']);

// Precision mode + show unconfirmed: disputed now visible.
assert.deepStrictEqual(vis(disputed, { precisionMode: true, showUnconfirmed: true }), ['person']);

// NOT precision mode: disputed visible even with the toggle off (all inference shown).
assert.deepStrictEqual(vis(disputed, { precisionMode: false, showUnconfirmed: false }), ['person']);
assert.deepStrictEqual(vis(verified, { precisionMode: false, showUnconfirmed: false }), ['person']);

// HA flags never become filter/visit labels, even with show-unconfirmed on.
const haPolluted = {
  person: true, porch_access: true, postal_delivery: true,
  animal_detected: true, _yolo: ['person'], _llm: {}
};
assert.deepStrictEqual(vis(haPolluted, { precisionMode: true, showUnconfirmed: false }), ['person']);
assert.deepStrictEqual(vis(haPolluted, { precisionMode: true, showUnconfirmed: true }), ['person']);
assert.deepStrictEqual(vis({
  porch_access: true, postal_delivery: true, clothes_drying: true
}, { precisionMode: false, showUnconfirmed: true }), []);

console.log('visibleLabels: all assertions passed');
