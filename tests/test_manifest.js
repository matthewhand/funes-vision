// Validates the PWA web app manifest (makes the gallery installable). No
// service worker — the manifest alone enables "Add to Home Screen" without any
// stale-cache risk. Run: node tests/test_manifest.js
const fs = require('fs');
const assert = require('assert');
const { src: html, meta } = require('./helpers/load.cjs');

const manifestPath = __dirname + '/../manifest.json';
assert(fs.existsSync(manifestPath), 'manifest.json not found at repo root');
const m = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));

for (const k of ['name', 'short_name', 'start_url', 'display', 'background_color', 'theme_color', 'icons']) {
  assert(k in m, `manifest missing required key: ${k}`);
}
assert(m.display === 'standalone', 'display should be standalone');
assert(Array.isArray(m.icons) && m.icons.length >= 1, 'manifest needs at least one icon');
for (const ic of m.icons) {
  assert(ic.src && ic.sizes && ic.type, 'each icon needs src/sizes/type');
}
// theme_color should match the <meta name="theme-color"> in index.html (#080c14).
assert.strictEqual(m.theme_color.toLowerCase(), meta('theme-color').toLowerCase(),
  'manifest theme_color must match index.html theme-color meta');

// index.html must link the manifest.
assert(/<link[^>]+rel="manifest"[^>]+href="manifest\.json"/.test(html),
  'index.html must <link rel="manifest" href="manifest.json">');

// The referenced icon files must exist.
for (const ic of m.icons) {
  assert(fs.existsSync(__dirname + '/../' + ic.src), `icon file missing: ${ic.src}`);
}

console.log('manifest: all assertions passed');
