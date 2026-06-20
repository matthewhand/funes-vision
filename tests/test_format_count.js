// Node assert test for the pure formatCount() helper in index.html — thousands-
// grouped integers for the status panel ("1,720" not "1720").
// Run: node tests/test_format_count.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:formatCount ===\n([\s\S]*?)\n\s*\/\/ === \/pure:formatCount/);
assert(m, 'formatCount sentinel block not found in index.html');
eval(m[1]); // defines formatCount

assert.strictEqual(formatCount(1720), '1,720');
assert.strictEqual(formatCount(0), '0');
assert.strictEqual(formatCount(999), '999');
assert.strictEqual(formatCount(8802), '8,802');
assert.strictEqual(formatCount(1234567), '1,234,567');
// Defensive: non-finite / nullish -> empty string (never "NaN"/"undefined").
assert.strictEqual(formatCount(null), '');
assert.strictEqual(formatCount(undefined), '');
assert.strictEqual(formatCount(NaN), '');

console.log('formatCount: all assertions passed');
