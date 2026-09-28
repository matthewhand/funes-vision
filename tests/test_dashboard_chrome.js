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
for (const sel of ['.tab-cluster', '#search-trigger', '#live-toggle', '#btn-help']) {
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
assert.ok(/class="search-trigger"/.test(html), '.search-trigger must remain');
assert.ok(/id="search-trigger"/.test(html), '#search-trigger must remain');
assert.ok(/id="search-popup"[^>]*role="dialog"/.test(html), '#search-popup dialog must remain');
assert.ok(/id="search-input"[^>]*class="search-popup-input"/.test(html), '#search-input must remain');
assert.match(html, /document\.addEventListener\('keydown', \(e\) => \{[\s\S]*?e\.metaKey \|\| e\.ctrlKey[\s\S]*?e\.key === 'k'/,
  'Ctrl/Cmd+K must open the search popup');
// Focus restore moved out of the Escape branch and into closeSearchPopup, which
// releases trapFocus (issue #76) — every close path restores focus now, not
// just Escape, so the assertion is on closeSearchPopup.
const closePopup = html.match(/function closeSearchPopup\(\) \{([\s\S]*?)\n      \}/);
assert(closePopup, 'closeSearchPopup not found');
assert.match(closePopup[1], /if \(searchPopupRelease\) \{ searchPopupRelease\(\); searchPopupRelease = null; \}/,
  'closeSearchPopup must release the palette focus trap');
assert.match(closePopup[1], /document\.activeElement === document\.body[\s\S]*?searchTrigger \|\| searchPopupInput\)\.focus\(\)/,
  'closeSearchPopup must put focus on the trigger when nothing else claimed it');
assert.ok(!/if \(e\.key === 'Escape' && searchPopup\.classList\.contains\('open'\)\) \{[\s\S]*?searchTrigger && searchTrigger\.focus\(\)/.test(html),
  'the manual Escape focus restore is superseded by the trapFocus release');

console.log('dashboard-chrome: all assertions passed');
