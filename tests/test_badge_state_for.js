// Node assert test for the pure badgeStateFor() helper in index.html — collapses
// the "awaiting Gemma" preliminary state to a plain verified badge when deep
// passes are OFF (so detector hits don't render as a wall of amber "label?").
// Run: node tests/test_badge_state_for.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:badgeStateFor ===\n([\s\S]*?)\n\s*\/\/ === \/pure:badgeStateFor/);
assert(m, 'badgeStateFor sentinel block not found in index.html');
eval(m[1]); // defines badgeStateFor

// deep passes OFF: preliminary -> verified (detector is the final word), others unchanged
assert.strictEqual(badgeStateFor('preliminary', false), 'verified');
assert.strictEqual(badgeStateFor('verified', false), 'verified');
assert.strictEqual(badgeStateFor('unknown', false), 'unknown');

// deep passes ON: everything passes through unchanged (the "?" is meaningful)
assert.strictEqual(badgeStateFor('preliminary', true), 'preliminary');
assert.strictEqual(badgeStateFor('verified', true), 'verified');

console.log('badgeStateFor: all assertions passed');
