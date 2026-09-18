// Contract: dashboard mode hides gallery review chrome (tabs, search, Live,
// Help) without removing IDs or hiding the dashboard itself.
// Run: node tests/test_dashboard_chrome.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

const setMode = html.match(/function setWorkspaceMode\(mode\) \{([\s\S]*?)\n    \}/);
assert(setMode, 'setWorkspaceMode not found');
assert.match(setMode[1], /document\.body\.classList\.toggle\('mode-dashboard',\s*isDash\)/,
  'setWorkspaceMode must toggle body.mode-dashboard');

const hideRules = [...html.matchAll(/body\.mode-dashboard[^{\n]*\{[^}]*\}/g)].map(m => m[0]);
assert.ok(hideRules.length, 'expected body.mode-dashboard CSS hide rule');
const hideCss = hideRules.join('\n');
assert.match(hideCss, /display:\s*none\s*!important/,
  'dashboard chrome hide must use display:none !important');
for (const sel of ['.tab-cluster', '.search-box', '#live-toggle', '#btn-help']) {
  assert.ok(html.includes('body.mode-dashboard ' + sel),
    'CSS hide rule must include ' + sel);
}
assert.ok(!/body\.mode-dashboard[^{]*#dashboard-view/.test(html),
  'must not hide #dashboard-view');
assert.ok(!/body\.mode-dashboard[^{]*#toolbar-actions/.test(html),
  'must not hide #toolbar-actions as a whole');

const currentRoute = html.match(/function currentRoute\(\) \{([\s\S]*?)\n    \}/);
assert(currentRoute, 'currentRoute not found');
assert.match(currentRoute[1], /path === '\/cameras'[\s\S]*ROUTE_DASHBOARD/,
  'currentRoute /cameras must be dashboard');

for (const id of ['stats-label', 'live-toggle', 'btn-help', 'sidebar-toggle',
                  'btn-refresh', 'toolbar-actions', 'dashboard-view']) {
  assert.ok(html.includes('id="' + id + '"'), id + ' must remain in markup');
}
assert.ok(/class="tab-cluster"/.test(html), '.tab-cluster must remain');
assert.ok(/class="search-box"/.test(html), '.search-box must remain');

console.log('dashboard-chrome: all assertions passed');
