// Node assert test for cameraDisplayName() — Status/HA nicknames.
// Webcam21/front → Front, Webcam22/back/dog → Back. Never print a LAN IP.
// Run: node tests/test_camera_display_name.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:cameraDisplayName ===\n([\s\S]*?)\n\s*\/\/ === \/pure:cameraDisplayName/);
assert(m, 'cameraDisplayName sentinel block not found in index.html');
eval(m[1]);

assert.strictEqual(cameraDisplayName('Webcam21'), 'Front');
assert.strictEqual(cameraDisplayName('/mnt/models/Webcam21'), 'Front');
assert.strictEqual(cameraDisplayName('front'), 'Front');
assert.strictEqual(cameraDisplayName('10.0.0.21'), 'Front');
assert.strictEqual(cameraDisplayName('10.0.0.21_01_20260618070200000_MOTDEC.jpg'), 'Front');

assert.strictEqual(cameraDisplayName('Webcam22'), 'Back');
assert.strictEqual(cameraDisplayName('back'), 'Back');
assert.strictEqual(cameraDisplayName('dog'), 'Back');
assert.strictEqual(cameraDisplayName('dogcam'), 'Back');
assert.strictEqual(cameraDisplayName('10.0.0.22_01_20260618080000000_MOTDEC.jpg'), 'Back');

assert.strictEqual(cameraDisplayName('10.0.0.99'), 'Camera');
assert.strictEqual(cameraDisplayName(''), 'Camera');
assert.strictEqual(cameraDisplayName(null), 'Camera');
assert.ok(!/10\.0\.0\./.test(cameraDisplayName('10.0.0.21')));
assert.ok(!/10\.0\.0\./.test(cameraDisplayName('10.0.0.99')));

// Status cameras block must nickname, never interpolate the FTP basename raw.
assert.ok(/cameraDisplayName\(c\.name\)/.test(html),
  'status cameras must print cameraDisplayName(c.name)');
assert.ok(!/<b>\$\{escapeHtml\(c\.name\)\}<\/b>/.test(html),
  'status cameras must not print raw c.name');

console.log('cameraDisplayName: all assertions passed');
