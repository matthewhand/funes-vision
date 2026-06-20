// Node assert test for the pure visitsSummary() helper in index.html — the
// Timeline count label, which must reveal the 300-visit render cap instead of
// silently truncating. Run: node tests/test_visits_summary.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:visitsSummary ===\n([\s\S]*?)\n\s*\/\/ === \/pure:visitsSummary/);
assert(m, 'visitsSummary sentinel block not found in index.html');
eval(m[1]); // defines visitsSummary

assert.strictEqual(visitsSummary(5, 300), '5 visits detected');
assert.strictEqual(visitsSummary(1, 300), '1 visit detected');     // singular
assert.strictEqual(visitsSummary(0, 300), '0 visits detected');
assert.strictEqual(visitsSummary(300, 300), '300 visits detected'); // exactly at cap, not truncated
assert.strictEqual(visitsSummary(412, 300), 'showing first 300 of 412 visits'); // truncated -> reveal

console.log('visitsSummary: all assertions passed');
