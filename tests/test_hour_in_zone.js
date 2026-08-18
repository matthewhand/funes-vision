// hourInZone / shortTimeZoneName. Run: node tests/test_hour_in_zone.js
const fs = require('fs');
const assert = require('assert');
const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const a = html.match(/pure:hourInZone ===\n([\s\S]*?)\n\s*\/\/ === \/pure:hourInZone/);
const b = html.match(/pure:shortTimeZoneName ===\n([\s\S]*?)\n\s*\/\/ === \/pure:shortTimeZoneName/);
assert(a && b, 'sentinels missing');
eval(a[1]);
eval(b[1]);

const sydneyAfternoon = new Date(Date.UTC(2026, 5, 18, 4, 0, 0)); // 14:00 AEST
assert.strictEqual(hourInZone(sydneyAfternoon, 'Australia/Sydney'), 14);
assert.strictEqual(hourInZone(sydneyAfternoon, 'UTC'), 4);
assert.strictEqual(hourInZone(null, 'UTC'), -1);
assert.strictEqual(hourInZone(new Date(NaN), 'UTC'), -1);

const name = shortTimeZoneName(sydneyAfternoon, 'Australia/Sydney');
assert.ok(name && /AE[SD]T|GMT\+1[01]|Australia/i.test(name), 'got ' + name);
assert.match(shortTimeZoneName(sydneyAfternoon, 'UTC'), /^(GMT|UTC)$/);

console.log('hourInZone/shortTimeZoneName: all assertions passed');
