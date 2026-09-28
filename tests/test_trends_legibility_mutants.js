// Mutation proof: each assertion in the trends contract must be able to fail.
const assert = require('assert');
const { src, createLoader } = require('./helpers/load.cjs');
const fs = require('fs');
const path = require('path');

const testPath = path.join(__dirname, 'test_trends_legibility.js');
const body = fs.readFileSync(testPath, 'utf8');

const MUTANTS = {
  'collapse the disclosure again': src.replace(
    'activity-chart-container" open>',
    'activity-chart-container">'
  ),
  'put the hint back in the axis': src
    .replace(/n === 0 \? ''/, 'n === 0 ? "" : "days &middot; top=12AM"')
    .replace(/\.chart-sublabel-hint/g, 'chart-sublabel-hint-removed'),
  'drop the single-day axis branch': src.replace(/n === 1 \?/g, 'n === 99 ?'),
  'drop the day-bar separator': src.replace(
    /\.chart-days \.chart-bar-wrapper \+ \.chart-bar-wrapper \{[\s\S]*?\n    \}/,
    ''
  ),
};

let killed = 0;
for (const [name, mutant] of Object.entries(MUTANTS)) {
  if (mutant === src) {
    console.log(`SKIP  ${name} -- mutation did not change the source`);
    continue;
  }
  // Re-run the contract body against the mutated document.
  let failed = false;
  try {
    const fn = new Function(
      'require',
      'module',
      '__filename',
      '__dirname',
      body
    );
    const shim = (id) => {
      if (id === 'assert') return assert;
      if (id === './helpers/load.cjs') {
        const L = createLoader(mutant, name);
        L.src = mutant;
        return L;
      }
      return require(id);
    };
    fn(shim, { exports: {} }, testPath, __dirname);
  } catch (e) {
    failed = true;
  }
  console.log(`${failed ? 'KILLED ' : 'SURVIVED'} ${name}`);
  if (failed) killed++;
}

const total = Object.keys(MUTANTS).length;
console.log(`\nmutants killed: ${killed}/${total}`);
assert.strictEqual(killed, total, 'every mutant must be caught by the contract');
