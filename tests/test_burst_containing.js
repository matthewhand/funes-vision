// burstContaining: summaries keyed by last frame still apply to earlier frames.
// Run: node tests/test_burst_containing.js
const fs = require('fs');
const assert = require('assert');
const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:burstContaining ===\n([\s\S]*?)\n\s*\/\/ === \/pure:burstContaining/);
assert(m, 'burstContaining sentinel missing');
eval(m[1]);

const bursts = {
  'last.jpg': { summary: 'walked up', images: ['a.jpg', 'b.jpg', 'last.jpg'] },
};
assert.strictEqual(burstContaining('last.jpg', bursts).summary, 'walked up');
assert.strictEqual(burstContaining('a.jpg', bursts).summary, 'walked up');
assert.strictEqual(burstContaining('nope.jpg', bursts), null);
assert.strictEqual(burstContaining('a.jpg', null), null);
assert.strictEqual(burstContaining('', bursts), null);

console.log('burstContaining: all assertions passed');
