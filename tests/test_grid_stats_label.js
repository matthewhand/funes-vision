// gridStatsLabel: never call stills "logs". Run: node tests/test_grid_stats_label.js
const fs = require('fs');
const assert = require('assert');
const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:gridStatsLabel ===\n([\s\S]*?)\n\s*\/\/ === \/pure:gridStatsLabel/);
assert(m, 'gridStatsLabel sentinel missing');
eval(m[1]);

assert.strictEqual(gridStatsLabel(7, 10), 'Showing 7 of 10 snapshots');
assert.strictEqual(gridStatsLabel(1, 1), 'Showing 1 of 1 snapshot');
assert.strictEqual(gridStatsLabel(0, 0), 'Showing 0 of 0 snapshots');
assert.ok(!/logs/i.test(gridStatsLabel(3, 8)));

console.log('gridStatsLabel: all assertions passed');
