// Node assert test for the pure isActivateKey() helper in index.html — whether a
// key should activate a button-like (div) control, so keyboard users can operate
// the date-sidebar items. Run: node tests/test_is_activate_key.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:isActivateKey ===\n([\s\S]*?)\n\s*\/\/ === \/pure:isActivateKey/);
assert(m, 'isActivateKey sentinel block not found in index.html');
eval(m[1]); // defines isActivateKey

assert.strictEqual(isActivateKey('Enter'), true);
assert.strictEqual(isActivateKey(' '), true);          // modern Space
assert.strictEqual(isActivateKey('Spacebar'), true);   // legacy Space
assert.strictEqual(isActivateKey('Tab'), false);
assert.strictEqual(isActivateKey('Escape'), false);
assert.strictEqual(isActivateKey('a'), false);
assert.strictEqual(isActivateKey(''), false);
assert.strictEqual(isActivateKey(undefined), false);

console.log('isActivateKey: all assertions passed');
