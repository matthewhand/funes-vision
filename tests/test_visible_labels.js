// Node assert test for the pure visibleLabels() helper in index.html. The
// YOLO-vs-LLM "disputed" path was removed, so every detected label is visible;
// the helper stays the chokepoint for filter/search/visit labels.
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
eval(block('visibleLabels')); // visibleLabels

// --- visibleLabels(analysis, aliases, opts) ---
const verified  = { person: true, _yolo: ['person'], _llm: {} };
const prelim    = { car: true, fast_pass: 'partial' };            // detector-only => preliminary
const gemmaOnly = { person: true, _yolo: [], _llm: {} };          // used to be disputed

const vis = (a) => Array.from(visibleLabels(a, {}, {})).sort();

assert.deepStrictEqual(vis(verified), ['person']);
assert.deepStrictEqual(vis(prelim), ['car']);
// The disputed path is gone: a Gemma-only claim is a normal verified label.
assert.deepStrictEqual(vis(gemmaOnly), ['person']);

// HA flags never become filter/visit labels.
const haPolluted = {
  person: true, porch_access: true, postal_delivery: true,
  animal_detected: true, _yolo: ['person'], _llm: {}
};
assert.deepStrictEqual(vis(haPolluted), ['person']);
assert.deepStrictEqual(vis({
  porch_access: true, postal_delivery: true, clothes_drying: true
}), []);

console.log('visibleLabels: all assertions passed');
