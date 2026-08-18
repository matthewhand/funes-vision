// Timeline/Objects/All are a pressed-button group, not a fake WAI-ARIA tablist
// (we never implemented arrow-key tablist behavior).
// Run: node tests/test_tab_aria.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

const grp = html.match(/<div class="tab-group" id="filter-tabs"[^>]*>/);
assert(grp, '#filter-tabs container not found');
assert(/role="group"/.test(grp[0]), '#filter-tabs must have role="group"');
assert(!/role="tablist"/.test(grp[0]), '#filter-tabs must not pretend to be a tablist');

const block = html.match(/<div class="tab-group" id="filter-tabs"[^>]*>([\s\S]*?)<\/div>/)[1];
const tabs = block.match(/<button[^>]*data-filter="[^"]*"[^>]*>/g) || [];
assert.strictEqual(tabs.length, 3, 'expected 3 view buttons, got ' + tabs.length);

let pressed = 0;
for (const t of tabs) {
  assert(!/role="tab"/.test(t), 'must not use role=tab without a tablist: ' + t);
  assert(/aria-pressed="(true|false)"/.test(t), 'each view needs aria-pressed: ' + t);
  assert(/title="[^"]{8,}"/.test(t), 'each view needs a descriptive title: ' + t);
  if (/aria-pressed="true"/.test(t)) pressed++;
}
assert.strictEqual(pressed, 1, 'exactly one view should start aria-pressed="true"');

const objects = tabs.find(t => /data-filter="objects"/.test(t));
assert(objects, 'objects view missing');
assert.ok(!/AI-verified/i.test(objects), 'Objects tooltip must not claim AI-verified');
assert.ok(/detected object/i.test(objects), 'Objects tooltip should say frames with a detected object');

console.log('tab-aria: pressed-button group + 3 views, one pressed');
