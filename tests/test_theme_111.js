// Contract for the frontend-only Appearance selector (issue #111).
const assert = require('assert');
const { src: html } = require('./helpers/load.cjs');

assert.match(html, /id="fv-theme-select"[^>]*aria-label="Colour theme"/);
for (const mode of ['system', 'light', 'dark']) {
  assert.match(html, new RegExp('<option value="' + mode + '">'));
}
assert.match(html, /localStorage\.getItem\(fvThemeKey\)/);
assert.match(html, /localStorage\.setItem\(fvThemeKey, fvThemeChoice\)/);
assert.match(html, /prefers-color-scheme: light/);
assert.match(html, /fvThemeMedia\.addEventListener\('change', systemSchemeChanged\)/);
assert.match(html, /document\.documentElement\.dataset\.theme = resolved/);
assert.match(html, /html\[data-theme="light"\]/);
assert.match(html, /color-scheme: light/);
assert.match(html, /fvThemeSelect\?\.addEventListener\('change'/);
assert.match(html, /id="filter-tabs"/, 'gallery controls must remain available');
console.log('theme-111: appearance settings and system preference contract passed');
