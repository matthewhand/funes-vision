// Node assert test for the pure parseFilenameFields() helper in index.html —
// the camera-filename parser (regex + event-type mapping + Unknown fallback),
// minus the Date/timezone work. Run: node tests/test_parse_filename.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:parseFilenameFields ===\n([\s\S]*?)\n\s*\/\/ === \/pure:parseFilenameFields/);
assert(m, 'parseFilenameFields sentinel block not found in index.html');
eval(m[1]); // defines parseFilenameFields

// A real motion-detection capture.
const f = parseFilenameFields('10.0.0.21_01_20260618064525084_MOTDEC.jpg');
assert.strictEqual(f.matched, true);
assert.strictEqual(f.ip, '10.0.0.21');
assert.strictEqual(f.channel, '01');
assert.strictEqual(f.hour, 6);
assert.strictEqual(f.dateStr, '2026-06-18');
assert.strictEqual(f.timeStr, '06:45:25');
assert.strictEqual(f.eventType, 'Motion Detected');   // MOTDEC -> friendly
assert.deepStrictEqual(f.parts, [2026, 6, 18, 6, 45, 25]);
assert.strictEqual(f.original, '10.0.0.21_01_20260618064525084_MOTDEC.jpg');

// GENERIC and other suffixes pass through unmapped.
assert.strictEqual(parseFilenameFields('10.0.0.22_02_20260101120000000_GENERIC.jpg').eventType, 'GENERIC');
assert.strictEqual(parseFilenameFields('10.0.0.22_02_20260101120000000_FOO.png').eventType, 'FOO');

// Case-insensitive extension still matches.
assert.strictEqual(parseFilenameFields('10.0.0.21_01_20260618064525084_MOTDEC.JPG').matched, true);

// Non-conforming names get the Unknown/Snapshot fallback.
const u = parseFilenameFields('not-a-camera-file.jpg');
assert.strictEqual(u.matched, false);
assert.strictEqual(u.ip, 'Unknown');
assert.strictEqual(u.channel, '--');
assert.strictEqual(u.hour, -1);
assert.strictEqual(u.dateStr, 'Unknown');
assert.strictEqual(u.eventType, 'Snapshot');
assert.strictEqual(u.parts, null);
assert.strictEqual(u.original, 'not-a-camera-file.jpg');

console.log('parseFilenameFields: all assertions passed');
