// Node assert test for the pure sidebarDefaultCollapsed() helper in index.html —
// decides the sidebar's initial collapsed state (explicit pref wins; else
// collapsed on small screens). Run: node tests/test_sidebar_collapsed.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:sidebarDefaultCollapsed ===\n([\s\S]*?)\n\s*\/\/ === \/pure:sidebarDefaultCollapsed/);
assert(m, 'sidebarDefaultCollapsed sentinel block not found in index.html');
eval(m[1]); // defines sidebarDefaultCollapsed

// explicit stored preference always wins
assert.strictEqual(sidebarDefaultCollapsed('true', 1440, 992), true);
assert.strictEqual(sidebarDefaultCollapsed('false', 400, 992), false);
assert.strictEqual(sidebarDefaultCollapsed(true, 1440, 992), true);
assert.strictEqual(sidebarDefaultCollapsed(false, 400, 992), false);
// no preference -> collapse on small screens, open on large
assert.strictEqual(sidebarDefaultCollapsed(null, 400, 992), true);
assert.strictEqual(sidebarDefaultCollapsed(null, 1440, 992), false);
assert.strictEqual(sidebarDefaultCollapsed(null, 992, 992), true);   // boundary inclusive
assert.strictEqual(sidebarDefaultCollapsed(null, 993, 992), false);
// default breakpoint = 992 when omitted
assert.strictEqual(sidebarDefaultCollapsed(null, 800), true);
assert.strictEqual(sidebarDefaultCollapsed(null, 1200), false);
// missing viewport -> not collapsed (safe default: show sidebar)
assert.strictEqual(sidebarDefaultCollapsed(null, undefined, 992), false);

console.log('sidebarDefaultCollapsed: all assertions passed');
