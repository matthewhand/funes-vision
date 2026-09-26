// Contract: the document head names the product (funes-vision) and ships a
// description + Open Graph / Twitter card metadata that points at a local
// asset (no remote CDN). Run: node tests/test_meta_head.js
const fs = require('fs');
const assert = require('assert');

const root = __dirname + '/..';
const html = fs.readFileSync(root + '/index.html', 'utf8');

const title = html.match(/<title>([^<]*)<\/title>/);
assert(title, 'index.html must have a <title>');
assert(/funes-vision/i.test(title[1]), 'title must name the product funes-vision');
assert(/camera stream viewer/i.test(title[1]), 'title should keep a sensible subtitle');

const meta = (key) => {
  const re = new RegExp(
    '<meta\\s+[^>]*(?:name|property)=["\']' + key + '["\'][^>]*content=["\']([^"\']+)["\']'
  );
  const m = html.match(re);
  return m && m[1];
};

const desc = meta('description');
assert(desc && desc.trim().length > 20, 'meta description must be present and non-trivial');

for (const p of ['og:title', 'og:description', 'og:image', 'og:type']) {
  assert(meta(p), 'missing ' + p);
}
assert(meta('twitter:card'), 'missing twitter:card');

const img = meta('og:image');
assert(!/^https?:/i.test(img), 'og:image must not be a remote URL/CDN: ' + img);
assert(
  fs.existsSync(root + '/' + img.replace(/^\.\//, '')),
  'og:image must point at an existing asset: ' + img
);

console.log('meta-head: all assertions passed');
