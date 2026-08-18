// hideLabelSwitchName: Settings hide/show rows. The name is the detections;
// aria-checked (via bindSwitch) carries on vs off. Never "Show …" while the
// row is currently visible. Run: node tests/test_hide_label_switch_name.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const m = html.match(/pure:hideLabelSwitchName ===\n([\s\S]*?)\n\s*\/\/ === \/pure:hideLabelSwitchName/);
assert(m, 'hideLabelSwitchName sentinel missing');
eval(m[1]);

assert.strictEqual(hideLabelSwitchName('person'), 'person detections');
assert.strictEqual(hideLabelSwitchName('car'), 'car detections');
assert.ok(!/^Show /i.test(hideLabelSwitchName('dog')), 'must not start with Show');
assert.ok(!/^Hide /i.test(hideLabelSwitchName('dog')), 'must not start with Hide');

// Call site must use the helper (not a hard-coded "Show ${label}").
assert.ok(
  /setAttribute\('aria-label',\s*hideLabelSwitchName\(label\)\)/.test(html),
  'renderBlacklistMenu must name the switch via hideLabelSwitchName'
);
assert.ok(
  !/aria-label',\s*`Show \$\{label\}/.test(html),
  'must not hard-code Show ${label} detections'
);

// First paint of the time-range label matches the all-day default JS writes.
const initial = html.match(/id="time-range-display">([^<]*)/);
assert(initial, '#time-range-display missing');
assert.strictEqual(initial[1], 'All day',
  'markup default must be All day (not 00:00 - 23:59) so first paint matches JS');

console.log('hideLabelSwitchName: all assertions passed');
