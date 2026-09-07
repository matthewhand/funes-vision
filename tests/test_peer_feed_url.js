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
assert.strictEqual(peerFeedUrl(true, '192.168.1.8', 'http:'), '/Webcam22/');
assert.strictEqual(peerFeedUrl(false, 'localhost', 'http:'), '/');
assert.ok(!html.includes('example.duckdns.org'),
  'index.html must not hardcode example.duckdns.org');

console.log('peerFeedUrl: all assertions passed');
