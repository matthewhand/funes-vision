// Node assert test for the pure badgeLabel() helper in index.html — the
// accessible name for a detection badge, conveying the lifecycle state that the
// colour + ? glyph alone show. Run: node tests/test_badge_label.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:badgeLabel ===\n([\s\S]*?)\n\s*\/\/ === \/pure:badgeLabel/);
assert(m, 'badgeLabel sentinel block not found in index.html');
eval(m[1]); // defines badgeLabel

assert.strictEqual(badgeLabel('person', 'verified'), 'person');         // confirmed = bare label
assert.strictEqual(badgeLabel('person', 'preliminary'), 'person, unconfirmed');
assert.strictEqual(badgeLabel('cat', undefined), 'cat');               // default = bare label
assert.strictEqual(badgeLabel('bird', 'somethingelse'), 'bird');       // unknown state = bare

console.log('badgeLabel: all assertions passed');
