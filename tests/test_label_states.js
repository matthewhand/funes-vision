// Node assert test for the pure computeLabelStates() helper in index.html.
// Covers the detection lifecycle (preliminary/verified/disputed) and the
// YOLO-consensus rule. Run: node tests/test_label_states.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:labelStates ===\n([\s\S]*?)\n\s*\/\/ === \/pure:labelStates/);
assert(m, 'labelStates sentinel block not found in index.html');
eval(m[1]); // defines computeLabelStates

const obj = (analysis, aliases) => Object.fromEntries(computeLabelStates(analysis, aliases));

// fast_pass present => Gemma hasn't ruled => everything preliminary
assert.deepStrictEqual(obj({ fast_pass: true, person: true }), { person: 'preliminary' });

// No fast_pass => Gemma ruled. YOLO corroborates => verified.
assert.deepStrictEqual(obj({ person: true, _yolo: ['person'] }), { person: 'verified' });

// Gemma claims a YOLO-capable object YOLO didn't see => disputed.
assert.deepStrictEqual(obj({ person: true, _yolo: [] }), { person: 'disputed' });

// YOLO saw it but Gemma didn't claim it => disputed.
assert.deepStrictEqual(obj({ _yolo: ['dog'] }), { dog: 'disputed' });

// BIRD is a YOLO class (backend class 14): a Gemma-only bird claim YOLO
// didn't corroborate must be DISPUTED, not verified. (regression guard)
assert.deepStrictEqual(obj({ bird: true, _yolo: [] }), { bird: 'disputed' });
assert.deepStrictEqual(obj({ bird: true, _yolo: ['bird'] }), { bird: 'verified' });

// Label beyond YOLO's classes => consensus impossible => verified.
assert.deepStrictEqual(obj({ alien_ufo: true, _yolo: [] }), { alien_ufo: 'verified' });

// Aliases canonicalise raw keys before classification.
assert.deepStrictEqual(obj({ kitty: true, _yolo: ['cat'] }, { kitty: 'cat' }), { cat: 'verified' });

// Defensive: null/undefined analysis => empty map.
assert.strictEqual(computeLabelStates(null).size, 0);
assert.strictEqual(computeLabelStates(undefined).size, 0);

// HA/e2b flags must not impersonate object identity (chips / visit types).
const HA_BOOLS = [
  'postal_delivery', 'porch_access', 'dog_walked', 'car_access',
  'enters_car', 'exits_car', 'opens_box', 'animal_detected',
  'approaching_house', 'leaving_house', 'weapon_detected', 'clothes_drying'
];
const haOnly = Object.fromEntries(HA_BOOLS.map(k => [k, true]));
haOnly.postal_how = 'on foot';
haOnly.animal_type = 'dog';
haOnly.car_outfit = 'red jacket';
haOnly.car_color = 'orange';
haOnly.car_make = 'toyota';
assert.deepStrictEqual(obj(haOnly), {});

// Mixed record: YOLO person stays; HA flags do not become labels.
assert.deepStrictEqual(obj({
  person: true, porch_access: true, postal_delivery: true,
  animal_detected: true, clothes_drying: true, _yolo: ['person']
}), { person: 'verified' });

// Aliasing an HA flag must not smuggle it in as a person chip.
assert.deepStrictEqual(
  obj({ porch_access: true, person: true, _yolo: ['person'] }, { porch_access: 'person' }),
  { person: 'verified' }
);
assert.deepStrictEqual(obj({ porch_access: true }, { porch_access: 'person' }), {});

console.log('computeLabelStates: all assertions passed');
