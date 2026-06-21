// Contract test: the Timeline/Objects/All filter tabs form a proper ARIA
// tablist (keyboard/screen-reader navigation) with descriptive tooltips.
// Run: node tests/test_tab_aria.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

// the container is a tablist
const grp = html.match(/<div class="tab-group" id="filter-tabs"[^>]*>/);
assert(grp, '#filter-tabs container not found');
assert(/role="tablist"/.test(grp[0]), '#filter-tabs must have role="tablist"');

// extract the three tab buttons
const block = html.match(/<div class="tab-group" id="filter-tabs"[^>]*>([\s\S]*?)<\/div>/)[1];
const tabs = block.match(/<button[^>]*data-filter="[^"]*"[^>]*>/g) || [];
assert.strictEqual(tabs.length, 3, 'expected 3 filter tabs, got ' + tabs.length);

let selected = 0;
for (const t of tabs) {
  assert(/role="tab"/.test(t), 'each tab needs role="tab": ' + t);
  assert(/aria-selected="(true|false)"/.test(t), 'each tab needs aria-selected: ' + t);
  assert(/title="[^"]{8,}"/.test(t), 'each tab needs a descriptive title tooltip: ' + t);
  if (/aria-selected="true"/.test(t)) selected++;
}
assert.strictEqual(selected, 1, 'exactly one tab should start aria-selected="true"');

console.log('tab-aria: tablist + 3 tabs (role/aria-selected/title), one selected');
