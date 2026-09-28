// Regression tests for issue #80 items 2 and 3 — the Timeline "All dates" dead
// end and the header camera chip that kept the previous camera's label.
//
// Pure helpers come out of their sentinel blocks (same style as
// tests/test_timeline_empty_state.js); the DOM half asserts the call
// sites use them, so a helper nobody calls still fails.
//
// Run: node tests/test_issue80_empty_and_chip.js
const assert = require('assert');
const { src: html, loadSentinel } = require('./helpers/load.cjs');

// Each helper is eval'd at module scope, so a helper that calls another
// (switchChipLabel -> cameraDisplayName) sees it — same style as
// tests/test_timeline_empty_state.js.
const grabSentinel = (name) => loadSentinel(name)[name];

eval(grabSentinel('timelineEmptyDayCopy')); // defines timelineEmptyDayCopy
eval(grabSentinel('cameraDisplayName'));    // defines cameraDisplayName
eval(grabSentinel('switchChipLabel'));      // defines switchChipLabel

// ---------------------------------------------------------------------------
// Item 2 — timelineEmptyDayCopy
// ---------------------------------------------------------------------------
// A single archive day is the whole point of the fix: one date repeated per
// IMAGE used to pass the `dates.length > 1` gate, so the "All dates" button
// re-rendered the identical message and was a dead end.
const oneDay = timelineEmptyDayCopy(
  Array.from({ length: 10 }, () => '20260618'), '20260618');
assert.strictEqual(oneDay.showAllDates, false,
  'one archive day must not offer the dead-end "All dates" button');
assert.ok(!/\bon this day\b/i.test(oneDay.message),
  'one-day copy must not say "on this day" (and be clickable): ' + oneDay.message);

// Several real days: the button is a real escape, so it must stay.
const manyDays = timelineEmptyDayCopy(['20260618', '20260617', '20260616'], '20260618');
assert.strictEqual(manyDays.showAllDates, true,
  'three distinct days must still offer "All dates"');
assert.ok(/on this day/i.test(manyDays.message), manyDays.message);

// Already on All Dates: no button (nowhere left to widen to) and no lie about
// being on a single day.
const onAll = timelineEmptyDayCopy(['20260618', '20260617', '20260616'], 'all');
assert.strictEqual(onAll.showAllDates, false,
  'on All Dates the "All dates" button is a dead end');
assert.ok(!/\bon this day\b/i.test(onAll.message),
  'copy on All Dates must not claim a single day: ' + onAll.message);
assert.notStrictEqual(onAll.message, manyDays.message,
  'copy must be derived from the active filter (all vs one day)');

// Unparseable dates must not manufacture extra days.
assert.strictEqual(timelineEmptyDayCopy(['Unknown', 'Unknown'], '20260618').showAllDates, false);
assert.strictEqual(timelineEmptyDayCopy([], '20260618').showAllDates, false);
assert.strictEqual(timelineEmptyDayCopy(null, null).showAllDates, false);

// renderEventsView must consume the helper, not the per-image date count.
const ev = html.match(/function renderEventsView\([^)]*\) \{([\s\S]*?)\n    function openEventPlayer/);
assert(ev, 'renderEventsView not found');
assert(/timelineEmptyDayCopy\(dates, state\.activeDateFilter\)/.test(ev[1]),
  'renderEventsView must derive the empty copy from timelineEmptyDayCopy');
assert(!/dates\.length\s*>\s*1/.test(ev[1]),
  'the dead-end gate on dates.length (per IMAGE, not per DAY) must be gone');

// ---------------------------------------------------------------------------
// Item 3 — switchChipLabel
// ---------------------------------------------------------------------------
assert.strictEqual(switchChipLabel('Webcam21', 'Front - Northside Driveway and Side Gate (CAM 01)'),
  'Front - Northside Driveway and Side Gate (CAM 01)',
  'the registry label must win when it is known');
assert.strictEqual(switchChipLabel('Webcam22', null), 'Back',
  'with no registry label, fall back to the folder-basename name');
assert.strictEqual(switchChipLabel('Webcam22', '   '), 'Back',
  'a blank registry label is not a label');
assert.strictEqual(switchChipLabel(null, ''), cameraDisplayName(null),
  'an unknown camera must agree with the h1/title rather than invent a name');

// The chip was written once from cameras().then() and never again, which is why
// it kept the PREVIOUS camera's name after a client-side switch.
const applyRoute = html.match(/async function applyRoute\(route\) \{([\s\S]*?)\n    \}/);
assert(applyRoute, 'applyRoute not found');
assert(/syncSwitchChip\(galleryId\)/.test(applyRoute[1]),
  'applyRoute must re-point the camera chip on every route change');
const sync = html.match(/function syncSwitchChip\(cameraId\) \{([\s\S]*?)\n    \}/);
assert(sync, 'syncSwitchChip not found');
assert(/switchChipLabel\(/.test(sync[1]), 'syncSwitchChip must use switchChipLabel');
assert(!/switchLabel\.textContent\s*=/.test(html),
  'the old one-shot switchLabel assignment must be gone (it never re-ran)');

console.log('issue80 empty-copy + camera-chip: all assertions passed');
