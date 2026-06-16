// Node assert test for the pure relativeTime() helper embedded in index.html.
// Extracts the function between sentinel comments and evaluates it, so the
// browser keeps one copy and we still unit-test it.  Run: node tests/test_relative_time.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:relativeTime ===\n([\s\S]*?)\n\s*\/\/ === \/pure:relativeTime/);
assert(m, 'relativeTime sentinel block not found in index.html');
eval(m[1]); // defines relativeTime in this scope

assert.strictEqual(relativeTime(1000, 1000), 'just now');
assert.strictEqual(relativeTime(1000, 1000 + 30), 'just now');
assert.strictEqual(relativeTime(1000, 1000 + 60), '1m ago');
assert.strictEqual(relativeTime(1000, 1000 + 300), '5m ago');
assert.strictEqual(relativeTime(1000, 1000 + 3600), '1h ago');
assert.strictEqual(relativeTime(1000, 1000 + 7200), '2h ago');
assert.strictEqual(relativeTime(1000, 1000 + 86400 * 2), '2d ago');
assert.strictEqual(relativeTime(NaN, 1000), '');     // bad input -> empty
assert.strictEqual(relativeTime(2000, 1000), '');    // future -> empty (no negatives)

console.log('relativeTime: all assertions passed');
