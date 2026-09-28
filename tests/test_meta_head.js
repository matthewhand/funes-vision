// Contract: the document head names the product (funes-vision) and ships a
// description + Open Graph / Twitter card metadata that points at a local
// asset (no remote CDN). Run: node tests/test_meta_head.js
const fs = require('fs');
const assert = require('assert');
const { src: html, meta } = require('./helpers/load.cjs');

const title = html.match(/<title>([^<]*)<\/title>/);
assert(title, 'index.html must have a <title>');
assert(/funes-vision/i.test(title[1]), 'title must name the product funes-vision');
assert(/camera stream viewer/i.test(title[1]), 'title should keep a sensible subtitle');

const desc = meta('description');
assert(desc && desc.trim().length > 20, 'meta description must be present and non-trivial');

for (const p of ['og:title', 'og:description', 'og:image', 'og:type']) {
  assert(meta(p), 'missing ' + p);
}
assert(meta('twitter:card'), 'missing twitter:card');

const img = meta('og:image');
assert(!/^https?:/i.test(img), 'og:image must not be a remote URL/CDN: ' + img);
assert(
  fs.existsSync(__dirname + '/../' + img.replace(/^\.\//, '')),
  'og:image must point at an existing asset: ' + img
);

console.log('meta-head: all assertions passed');
