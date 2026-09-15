// Node assert tests for getHAFlags() / entryCaption() in index.html.
// HA badges skip empty/"none"/false; captions prefer description, else 1–2
// synthesized facts. Run: node tests/test_ha_flags.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
function block(name) {
  const m = html.match(new RegExp('pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*\\/\\/ === \\/pure:' + name));
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
}
eval(block('getHAFlags'));
eval(block('entryCaption'));

const labels = (flags) => flags.map(f => f.isString ? f.label + '=' + f.value : f.label).sort();

// --- getHAFlags ---
assert.deepStrictEqual(getHAFlags(null), []);
assert.deepStrictEqual(getHAFlags(undefined), []);
assert.deepStrictEqual(getHAFlags({}), []);

// False / empty / "none" must not render a badge.
assert.deepStrictEqual(getHAFlags({
  postal_delivery: false,
  postal_how: 'none',
  animal_detected: false,
  animal_type: 'none',
  car_outfit: '',
  car_color: '  ',
  car_make: 'NONE',
  porch_access: false,
  dog_walked: false,
}), []);

// String "false" / "unknown" are sentinels, not values.
assert.deepStrictEqual(getHAFlags({ postal_how: 'false', animal_type: ' False ' }), []);
assert.deepStrictEqual(getHAFlags({ car_make: 'unknown', car_color: 'Unknown' }), []);

// Orphan strings (parent boolean false/absent) must not badge.
assert.deepStrictEqual(getHAFlags({
  postal_how: 'on foot',
  animal_type: 'dog',
  car_make: 'toyota',
  car_access: false,
}), []);

// True booleans and real string values do render.
assert.deepStrictEqual(labels(getHAFlags({
  postal_delivery: true,
  postal_how: 'on foot',
  porch_access: true,
  animal_detected: true,
  animal_type: 'dog',
  person: true,          // not an HA flag
  car: true,
})), ['animal_detected', 'animal_type=dog', 'porch_access', 'postal_delivery', 'postal_how=on foot']);

// Mixed record: only the active HA fields.
assert.deepStrictEqual(labels(getHAFlags({
  clothes_drying: true,
  postal_how: 'none',
  animal_type: 'none',
  car_access: false,
})), ['clothes_drying']);

// --- entryCaption ---
assert.strictEqual(entryCaption(null), '');
assert.strictEqual(entryCaption(undefined), '');
assert.strictEqual(entryCaption({}), '');

// Prefer a non-empty description; ignore whitespace-only.
assert.strictEqual(entryCaption({ description: 'A person at the gate', porch_access: true }),
  'A person at the gate');
assert.strictEqual(entryCaption({ description: '   ', porch_access: true }),
  'Someone at the porch');

assert.strictEqual(entryCaption({ postal_delivery: true, postal_how: 'on foot' }),
  'Postal delivery on foot');
assert.strictEqual(entryCaption({ porch_access: true }), 'Someone at the porch');
assert.strictEqual(entryCaption({ dog_walked: true }), 'Person walking a dog');
assert.strictEqual(entryCaption({ animal_detected: true, animal_type: 'dog' }),
  'Dog in the yard');
assert.strictEqual(entryCaption({ clothes_drying: true }), 'Clothes on the line');
assert.strictEqual(entryCaption({ car_access: true }), 'Car in the driveway');

// postal_how "none" / missing must not append junk.
assert.strictEqual(entryCaption({ postal_delivery: true, postal_how: 'none' }),
  'Postal delivery');
assert.strictEqual(entryCaption({ postal_delivery: true }), 'Postal delivery');
assert.strictEqual(entryCaption({ postal_delivery: true, postal_how: 'van' }),
  'Postal delivery by van');

// Combine at most two facts; skip redundant animal when dog is walked.
assert.strictEqual(entryCaption({
  postal_delivery: true, postal_how: 'on foot', porch_access: true, clothes_drying: true
}), 'Postal delivery on foot. Someone at the porch');
assert.strictEqual(entryCaption({
  dog_walked: true, animal_detected: true, animal_type: 'dog'
}), 'Person walking a dog');

// animal_type none → generic, not "None in the yard".
  assert.strictEqual(entryCaption({ animal_detected: true, animal_type: 'none' }),
    'Animal in the yard');

  // YOLO-only visits (no HA flags, no description) used to render no
  // sentence at all. Fall back to the detector labels the fast pass saw.
  assert.strictEqual(entryCaption({ person: true }), 'person');
  assert.strictEqual(entryCaption({ car: true, person: true }), 'car, person');
  assert.strictEqual(entryCaption({ _yolo: { dog: true } }), 'dog');
  assert.strictEqual(entryCaption({ _yolo: { person: true }, porch_access: true }),
    'Someone at the porch');  // HA facts still win
  assert.strictEqual(entryCaption({ fast_pass: 'negative', person: true }),
    'person');  // fast_pass is not a label

console.log('haFlags/entryCaption: all assertions passed');
