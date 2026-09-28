// Contract: the Motion activity chart must be discoverable without clicking,
// and its day axis must carry dates only.
//
// Two defects this pins:
//
//  1. DISCOVERABILITY. The chart lives in
//     `<details class="sidebar-section activity-chart-container">` and nothing
//     ever set `open`, so the app's only trends surface was permanently
//     collapsed AND never drawn -- `renderActivityChart()` early-returns on
//     `!chartPanel.open`. The Motion tab showed a photo grid with no hint that
//     a chart existed.
//
//  2. AXIS CONTENT. The day axis rendered
//     `<span>{first}</span><span>days - top=12AM</span><span>{last}</span>`.
//     The middle slot is a fixed string, not a date, so it read as a third date
//     label and collided with its neighbours in the 196px sidebar. A one-day
//     range printed the same date twice: "6/18 | days - top=12AM | 6/18".
//     The hint now lives in the "By day" sublabel and the axis is dates only,
//     with a single-day range printing one label.
const assert = require('assert');
const { src, css, rule, grabFn } = require('./helpers/load.cjs');

// ---- 1. the disclosure ships open -----------------------------------------
const details = src.match(
  /<details class="sidebar-section activity-chart-container"([^>]*)>/
);
assert.ok(details, 'the activity chart host must still be a <details>');
assert.ok(
  /\bopen\b/.test(details[1]),
  'the activity chart must ship OPEN. It is the only trends surface, and ' +
    'renderActivityChart() returns early while it is closed, so a collapsed ' +
    'host means no chart is ever drawn.'
);

// ---- 2. the hint is not in the axis ---------------------------------------
const renderFn = grabFn('renderActivityChart');
assert.ok(renderFn, 'renderActivityChart must exist');

assert.ok(
  !/days\s*&middot;\s*top=12AM/.test(renderFn),
  'the "days - top=12AM" hint must not be emitted into .chart-x-axis -- it ' +
    'reads as a date label and collides with the real dates beside it'
);
assert.ok(
  /chart-sublabel-hint/.test(src),
  'the hint must exist as .chart-sublabel-hint in the "By day" sublabel'
);

// ---- 3. a one-day range must not print its date twice ----------------------
assert.ok(
  /n === 1/.test(renderFn) && /sortedDays\[0\]/.test(renderFn),
  'the day axis needs a single-day branch that prints the date once'
);
assert.ok(
  /Math\.floor\(n \/ 2\)/.test(renderFn),
  'a multi-day axis should label first / middle / last, not first / hint / last'
);

// ---- 4. adjacent day bars stay visually separate ---------------------------
// Six days in a 196px sidebar are ~30px each and their red hour segments line
// up at matching heights, so the bars fuse into one solid band.
const days = rule('.chart-days');
assert.ok(days, '.chart-days must exist');
assert.ok(
  /gap:\s*\d/.test(days),
  '.chart-days needs its own gap so day bars do not touch'
);
assert.ok(
  /\.chart-days \.chart-bar-wrapper \+ \.chart-bar-wrapper/.test(css()),
  'adjacent day bars need a separator, or a multi-day chart reads as one bar'
);

// ---- 5. the wrapper floor must survive -------------------------------------
// .chart-hit is the 24px WCAG 2.5.8 target; min-height keeps a wrapper from
// reporting a 0x0 box. Guard against a well-meaning tidy-up removing them.
const wrapper = rule('.chart-bar-wrapper');
assert.ok(/min-height:\s*24px/.test(wrapper), '.chart-bar-wrapper keeps min-height:24px');
assert.ok(/min-width:\s*0/.test(wrapper), '.chart-bar-wrapper keeps min-width:0');

console.log('trends legibility: all assertions passed');
