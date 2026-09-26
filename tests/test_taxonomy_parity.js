// Node assert test that the SPA and the Python taxonomy.py agree (#58):
//   1. the embedded offline fallback equals the canonical /api/taxonomy payload;
//   2. the pure consumers read through haTaxonomy() instead of embedding lists;
//   3. setTaxonomy() actually swaps the runtime source of truth.
// Run: node tests/test_taxonomy_parity.js
const fs = require('fs');
const assert = require('assert');
const { execFileSync } = require('child_process');
const path = require('path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
function block(name) {
  const m = html.match(new RegExp('pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*\\/\\/ === \\/pure:' + name));
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
}

// Canonical payload straight from the Python source of truth.
const payloadJson = execFileSync('python3', [
  '-c', 'import json,taxonomy;print(json.dumps(taxonomy.payload(),sort_keys=True))',
], { cwd: root, encoding: 'utf8' });
const canonical = JSON.parse(payloadJson);

// 1. Embedded fallback must equal the Python source of truth.
eval(block('taxonomy'));
const fallback = taxonomyFallback();
assert.deepStrictEqual(fallback.ha_flag_keys, canonical.ha_flag_keys);
assert.deepStrictEqual(fallback.ha_string_parents, canonical.ha_string_parents);
assert.deepStrictEqual(fallback.ha_string_sentinels, canonical.ha_string_sentinels);
assert.deepStrictEqual(fallback.dropped_flags, canonical.dropped_flags);
assert.strictEqual(fallback.default_tz, canonical.default_tz);

// 2. The pure consumers must not embed their own HA flag list.
const labelBlock = block('labelStates');
const haBlock = block('getHAFlags');
for (const [name, src] of [['labelStates', labelBlock], ['getHAFlags', haBlock]]) {
  assert(src.includes('haTaxonomy()'), name + ' must read the canonical taxonomy');
  for (const flag of ['weapon_detected', 'approaching_house', 'postal_delivery']) {
    assert(!src.includes("'" + flag + "'"),
      name + ' must not embed the HA flag list (found ' + flag + ')');
  }
}

// 3. setTaxonomy() swaps the runtime source; the payload is actually consumed.
eval(labelBlock);
eval(haBlock);
eval(block('resolveDisplayTz'));
setTaxonomy({
  ha_flag_keys: ['porch_access'],
  ha_string_parents: {},
  ha_string_sentinels: ['none'],
  dropped_flags: [],
  default_tz: 'UTC',
});
assert.deepStrictEqual(getHAFlags({ porch_access: true, postal_delivery: true }),
  [{ label: 'porch_access', value: true, isString: false }]);
assert.deepStrictEqual(
  Object.fromEntries(computeLabelStates({ porch_access: true, person: true })),
  { person: 'preliminary' });
assert.strictEqual(resolveDisplayTz(''), 'UTC'); // default_tz flows through

// Restore the fallback and confirm the canonical flags come back.
setTaxonomy(null);
assert.deepStrictEqual(getHAFlags({ postal_delivery: true }),
  [{ label: 'postal_delivery', value: true, isString: false }]);
assert.strictEqual(resolveDisplayTz(''), canonical.default_tz);

console.log('taxonomy parity: all assertions passed');
