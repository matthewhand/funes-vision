// Contract test: the toast should read as an ambient SURFACE notification
// (panel bg + border), not a solid-accent block that mimics a primary CTA — the
// accent is reserved for the leading icon. Run: node tests/test_toast_style.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/\.toast\s*\{([\s\S]*?)\}/);
assert(m, '.toast rule not found');
const block = m[1];

assert(/border:/.test(block), '.toast should have a border (surface notification)');
assert(/background:\s*var\(--bg-/.test(block),
  '.toast background should be a surface var (--bg-*), not the accent CTA colour');
assert(!/background:\s*var\(--accent-color\)/.test(block),
  '.toast must not use var(--accent-color) as its background');
// The accent should still appear, on the icon.
const iconBlock = (html.match(/\.toast i\s*\{([\s\S]*?)\}/) || [, ''])[1];
assert(/var\(--accent/.test(iconBlock), '.toast i should carry the accent colour');

console.log('toast-style: all assertions passed');
