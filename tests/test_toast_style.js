// Contract test: the toast should read as an ambient SURFACE notification
// (panel bg + border), not a solid-accent block that mimics a primary CTA — the
// accent is reserved for the leading icon. Run: node tests/test_toast_style.js
const assert = require('assert');
const { rule } = require('./helpers/load.cjs');
const block = rule('.toast');

assert(/border:/.test(block), '.toast should have a border (surface notification)');
assert(/background:\s*var\(--bg-/.test(block),
  '.toast background should be a surface var (--bg-*), not the accent CTA colour');
assert(!/background:\s*var\(--accent-color\)/.test(block),
  '.toast must not use var(--accent-color) as its background');
// The accent should still appear, on the icon.
assert(/var\(--accent/.test(rule('.toast i')), '.toast i should carry the accent colour');

console.log('toast-style: all assertions passed');
