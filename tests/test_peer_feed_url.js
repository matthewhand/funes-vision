// peerFeedUrl must not hardcode a personal DuckDNS host.
// Run: node tests/test_peer_feed_url.js
const fs = require('fs');
const assert = require('assert');
const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:peerFeedUrl ===\n([\s\S]*?)\n\s*\/\/ === \/pure:peerFeedUrl/);
assert(m, 'peerFeedUrl sentinel missing');
eval(m[1]);

assert.strictEqual(peerFeedUrl(true, 'dogcam.example.org', 'https:'),
  'https://webcam.example.org/');
assert.strictEqual(peerFeedUrl(false, 'webcam.example.org', 'http:'),
  'http://dogcam.example.org/');
assert.strictEqual(peerFeedUrl(true, '192.168.1.8', 'http:'), '/');
assert.strictEqual(peerFeedUrl(false, 'localhost', 'http:'), '/Webcam22/');
assert.ok(!html.includes('example.duckdns.org'),
  'index.html must not hardcode example.duckdns.org');

// cameraFeedKind: the registry (`kind`) drives front/back; the legacy
// Webcam22/dogcam convention is only the fallback when the registry is
// unavailable. Run: kept here so the JS suite count is unchanged.
const ck = html.match(/pure:cameraFeedKind ===\n([\s\S]*?)\n\s*\/\/ === \/pure:cameraFeedKind/);
assert(ck, 'cameraFeedKind sentinel missing');
eval(ck[1]);
const reg = [
  { id: 'Webcam21', kind: 'front' },
  { id: 'Webcam22', kind: 'back' },
];
assert.strictEqual(cameraFeedKind(reg, '/Webcam22/', '192.168.1.8'), 'back');
assert.strictEqual(cameraFeedKind(reg, '/Webcam21/', '192.168.1.8'), 'front');
assert.strictEqual(cameraFeedKind(reg, '/', 'dogcam.example.org'), 'back');
assert.strictEqual(cameraFeedKind(reg, '/', 'webcam.example.org'), 'front');
// Configured kind wins over the legacy folder heuristic.
assert.strictEqual(cameraFeedKind([{ id: 'Webcam21', kind: 'back' }], '/Webcam21/', ''), 'back');
// Empty/absent registry falls back to the legacy convention.
assert.strictEqual(cameraFeedKind([], '/Webcam22/', ''), 'back');
assert.strictEqual(cameraFeedKind(null, '/', 'dogcam.example.org'), 'back');
assert.strictEqual(cameraFeedKind([], '/', 'localhost'), 'front');

// getFeedProfile must derive from metadata, not branch on LAN IPs.
assert.ok(!/10\.0\.0\.2[12]/.test(html),
  'index.html must not hardcode 10.0.0.21/22');

console.log('peerFeedUrl: all assertions passed');
