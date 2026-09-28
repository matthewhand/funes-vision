// peerFeedUrl must not hardcode a personal DuckDNS host.
// Run: node tests/test_peer_feed_url.js
const assert = require('assert');
const { src: html, appSource, loadSentinel } = require('./helpers/load.cjs');
const sentinels = loadSentinel('peerFeedUrl');
eval(sentinels.peerFeedUrl);

assert.strictEqual(peerFeedUrl(true, 'dogcam.example.org', 'https:'),
  'https://webcam.example.org/');
assert.strictEqual(peerFeedUrl(false, 'webcam.example.org', 'http:'),
  'http://dogcam.example.org/');
assert.strictEqual(peerFeedUrl(true, '192.168.1.8', 'http:'), '/');
assert.strictEqual(peerFeedUrl(false, 'localhost', 'http:'), '/Webcam22/');

// cameraFeedKind: the registry (`kind`) drives front/back; the legacy
// Webcam22/dogcam convention is only the fallback when the registry is
// unavailable. Run: kept here so the JS suite count is unchanged.
eval(loadSentinel('cameraFeedKind').cameraFeedKind);
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

// The two bans below are "this string is ABSENT", so an empty haystack passes
// them trivially. The inline script is the haystack that is about to become
// js/*.js, so assert it is non-empty before grepping it -- and grep BOTH the
// document and the script, so the ban still covers the code after the split
// moves it out of index.html.
const app = appSource();
assert.ok(app.length > 1000,
  'the inline <script> came back at ' + app.length + ' bytes; a banned-pattern grep over ' +
  'an empty script passes vacuously. Re-point appSource() at the new module files.');
for (const [label, haystack] of [['index.html', html], ['inline <script>', app]]) {
  assert.ok(!haystack.includes('example.duckdns.org'),
    label + ' must not hardcode example.duckdns.org');
  assert.ok(!/10\.0\.0\.2[12]/.test(haystack),
    label + ' must not hardcode 10.0.0.21/22');
}

// getFeedProfile must derive from metadata, not branch on LAN IPs (covered by
// the 10.0.0.2x ban above).

console.log('peerFeedUrl: all assertions passed');
