// Contract: the sidebar, the header count, the card clock and the "This view"
// sparkline must all be readable rather than truncated, clipped or half-drawn.
//
// MEASURED BUGS (headless Chromium, synthetic fixtures, tools/screenshots/proxy.py):
//
//  1. SIDEBAR VOID. 1440x1000: <aside> 196x880, its three sections ending at
//     y=477, i.e. 484px of nothing under a 32px collapsed "Saved" strip. The
//     panel is capped to the viewport (max-height: calc(100vh - 120px)) but its
//     sections are content-sized, and #date-list carried a second hard cap of
//     its own (280px) -- so even a 34-day archive stopped at y=675 and left
//     286px dead.
//
//  2. TRUNCATED COUNT. #stats-label rendered "Showing 10 of …": a 100px box for
//     a 164px string, because `max-width: 14ch; overflow: hidden;
//     text-overflow: ellipsis; white-space: nowrap` sat on a flex child of a
//     nowrap row. At 1024px the same rule squeezed it to 12px and at 961px to
//     0px -- the sentence was gone, not shortened.
//
//  3. CARD CLOCK. Cards printed "05:00:00 pm", "02:30:28 pm", "12:00:00 pm",
//     "08:00:00 am": lowercase, and the hour cycle was whatever the runtime's
//     CLDR data resolved en-AU to. Two hundred pixels away the hour axis says
//     "12 AM", in uppercase, and a build that resolved the cycle to 24-hour
//     would print midnight as "00:00:00" with no day period at all -- the same
//     string as noon.
//
//  4. SPARKLINE. Under "THIS VIEW" the 24 bars were 3px inline-block hairlines
//     2-18px tall in a line-height:0 box, so a 10-frame view drew one 18px green
//     peak above a row of 2px stubs: a stray tick, not a chart, with no baseline
//     and no axis.
//
// These are static assertions -- they pin the recipe, not the layout. The
// browser measurements quoted above are the layout proof. The mutation block at
// the bottom re-runs check() against eleven pre-fix copies of the document to
// prove each assertion can actually fail.
/* global gridStatsLabel, timeStampLabel */

const assert = require('assert');
const {
  src, css, rule, ruleN, decl, mediaBodies, markup, hasId, grabFn,
  createLoader, loadSentinel,
} = require('./helpers/load.cjs');

// The width below which the header count drops to its own row under the tabs.
// 1360px is where the sentence stops fitting beside the tabs on one line
// (measured: 164px of text needed at 1440, squeezed to 146px over two lines at
// 1280, 12px at 1024).
const OWN_ROW = /max-width:\s*1359px/;

function check(html, label) {
  const L = createLoader(html, label);

  // ---- 1. the sidebar must be framed, not truncated ------------------------
  // The trailing section is pinned to the bottom of the panel, so the space
  // below the content reads as a deliberate gap between two anchored groups
  // rather than as a list that ran out half way down.
  const tail = L.rule('aside > :last-child');
  assert.ok(
    /margin-top:\s*auto/.test(tail),
    `${label}: the trailing sidebar section needs margin-top: auto. On a 1000px ` +
      'window the panel is 880px tall and its sections end at y=477, so without ' +
      `it the collapsed 32px "Saved" strip floats above 484px of void. got: ${tail.trim()}`
  );

  // #date-list had a second, content-independent cap: 280px, reached by 12 rows.
  // The aside is already viewport-capped and scrolls, so a real archive has to
  // be able to fill the panel rather than stopping short of it.
  const dateList = L.rule('.date-list');
  assert.ok(
    /max-height:\s*none/.test(dateList),
    `${label}: #date-list must not cap its own height -- the aside's viewport cap ` +
      'is the only scroll boundary it needs. A 34-row list measured 1458px of ' +
      `content in a 280px box. got: ${dateList.trim()}`
  );

  // ---- 2. the header count must be readable end to end ---------------------
  const stats = L.rule('#stats-label.stats-info');
  for (const banned of [/text-overflow/, /max-width:\s*14ch/, /white-space:\s*nowrap/, /overflow:\s*hidden/]) {
    assert.ok(
      !banned.test(stats),
      `${label}: #stats-label must not use ${banned} -- it is a flex child of a ` +
        'nowrap row, so the cap ellipsised instead of wrapping and the user read ' +
        '"Showing 10 of ..." (a 100px box around 164px of text) at 1440px. got: ' +
        stats.trim()
    );
  }
  assert.strictEqual(
    decl(stats, 'min-width'), '0',
    `${label}: #stats-label needs min-width: 0 so it wraps to a second line rather ` +
      'than shrinking to nothing (measured 12px wide at 1024px, 0px at 961px)'
  );

  // Below the breakpoint the count drops to its own row under the tabs, which is
  // the treatment .toolbar-bar's .tab-cluster already gets at 768px.
  const ownRow = L.mediaBodies(OWN_ROW).find((b) => b.includes('#stats-label'));
  assert.ok(ownRow, `${label}: no ${OWN_ROW} block mentions #stats-label`);
  assert.ok(
    /flex:\s*1 1 100%/.test(ownRow),
    `${label}: below ${OWN_ROW} the count must take its own row (flex: 1 1 100%), ` +
      `not share the tabs' line. got: ${ownRow.trim().slice(0, 200)}`
  );
  const cluster = ownRow.match(/\.header-content \.tab-cluster\s*\{([^}]*)\}/);
  assert.ok(cluster, `${label}: the ${OWN_ROW} block must let .tab-cluster wrap`);
  assert.ok(
    /flex-wrap:\s*wrap/.test(cluster[1]),
    `${label}: .tab-cluster needs flex-wrap: wrap in that block, or the count is ` +
      `squeezed instead of dropped to a second row. got: ${cluster[1].trim()}`
  );

  // A large archive must still produce a sentence worth reading. Thousands
  // grouping is the only change: 7/10 and 0/0 stay byte-identical to before.
  eval(L.loadSentinel('gridStatsLabel').gridStatsLabel);
  assert.strictEqual(
    gridStatsLabel(1234, 12345), 'Showing 1,234 of 12,345 snapshots',
    'a five-figure total must be grouped, not printed as a digit run'
  );
  assert.strictEqual(
    gridStatsLabel(7, 10), 'Showing 7 of 10 snapshots',
    'grouping must not change the short form (pinned byte for byte by ' +
      'tests/test_grid_stats_label.js)'
  );

  // ---- 3. the card clock states its hour cycle and its case ---------------
  // Every clock string used to come from an en-AU toLocaleTimeString, which
  // left the hour cycle to the locale data and spelled the day period lowercase.
  assert.ok(
    !/toLocaleTimeString\(\s*'en-AU'/.test(html),
    `${label}: no en-AU toLocaleTimeString clock is left -- it left the hour cycle ` +
      'to the locale data and produced lowercase "12:00:00 pm" beside the axis\'s ' +
      '"12 AM". Every clock goes through timeStampLabel().'
  );

  eval(L.loadSentinel('timeStampLabel').timeStampLabel);

  // Midnight and noon are the pair the old formatter made indistinguishable
  // whenever the cycle resolved to 24-hour ("00:00:00", no day period at all).
  const SYD = 'Australia/Sydney';
  const at = (h, m, s) => timeStampLabel(new Date(Date.UTC(2026, 5, 18, h, m, s)), 'UTC', true);
  assert.strictEqual(at(0, 0, 0), '12:00:00 AM', 'midnight must read 12:00:00 AM');
  assert.strictEqual(at(0, 30, 0), '12:30:00 AM', '00:30 must read 12:30:00 AM');
  assert.strictEqual(at(12, 0, 0), '12:00:00 PM', 'noon must read 12:00:00 PM');
  assert.notStrictEqual(at(0, 0, 0), at(12, 0, 0), 'midnight and noon must differ');
  assert.strictEqual(at(8, 0, 0), '08:00:00 AM', 'measured "08:00:00 am"');
  assert.strictEqual(at(17, 0, 0), '05:00:00 PM', 'measured "05:00:00 pm"');
  assert.strictEqual(at(23, 59, 59), '11:59:59 PM', 'the last second of the day');
  // The zone is the caller's, so a Sydney wall clock still reads Sydney time.
  assert.strictEqual(
    timeStampLabel(new Date(Date.UTC(2026, 5, 18, 0, 0, 0)), SYD, true), '10:00:00 AM',
    'timeStampLabel must honour the display zone, not the browser zone'
  );
  assert.strictEqual(
    timeStampLabel(new Date(Date.UTC(2026, 5, 18, 17, 0, 0)), SYD, false), '03:00 AM',
    'withSeconds:false is the audit-trail form (hours and minutes only), and ' +
      '17:00Z is 03:00 the next morning in Sydney'
  );
  assert.strictEqual(timeStampLabel(null, SYD, true), '', 'a missing date is empty, not "NaN"');
  assert.strictEqual(timeStampLabel(new Date(NaN), SYD, true), '', 'an invalid date is empty');
  assert.strictEqual(
    timeStampLabel(new Date(Date.UTC(2026, 5, 18, 0, 0, 0)), 'Not/AZone', true), '',
    'an unresolvable zone is empty, not an uncaught RangeError'
  );

  // Both call sites, so a reintroduced formatter cannot pass by sitting unused.
  assert.ok(
    /timeStampLabel\(dateObj, DISPLAY_TZ, true\)/.test(html),
    `${label}: the card stamp must come from timeStampLabel(dateObj, DISPLAY_TZ, true)`
  );
  assert.ok(
    /timeStampLabel\(new Date\(r\.started \* 1000\), DISPLAY_TZ, false\)/.test(html),
    `${label}: the audit-trail stamp must come from timeStampLabel(..., false)`
  );

  // ---- 4. the sparkline is a chart, not a stray tick -----------------------
  const spark = L.rule('.sparkline');
  assert.ok(
    /display:\s*flex/.test(spark) && /align-items:\s*flex-end/.test(spark),
    `${label}: .sparkline must be a flex row of columns standing on one baseline, ` +
      `so the shape of the day is readable. got: ${spark.trim()}`
  );
  assert.ok(
    /border-bottom:\s*1px solid/.test(spark),
    `${label}: .sparkline needs a bottom border -- the bars have to stand on ` +
      'something. The old line-height:0 box had no baseline at all.'
  );

  const panelFn = L.grabFn('renderSystemPanel');
  const bars = panelFn.match(/const insSpark =[\s\S]*?\.join\(''\);/);
  assert.ok(bars, `${label}: the insSpark bar template not found`);
  for (const banned of [/width:3px/, /margin-right:1px/, /vertical-align:bottom/]) {
    assert.ok(
      !banned.test(bars[0]),
      `${label}: the sparkline bars must not be ${banned} -- a 3px inline-block ` +
        `hairline 2-18px tall read as one stray tick. got: ${bars[0].trim()}`
    );
  }
  assert.ok(
    /flex:1 1 0/.test(bars[0]) && /min-width:0/.test(bars[0]),
    `${label}: the bars must be flex columns that share the panel width, so no bar ` +
      `can collapse to a hairline. got: ${bars[0].trim()}`
  );
  assert.ok(
    /Math\.max\(4,/.test(bars[0]),
    `${label}: an hour with no frames needs a visible floor (Math.max(4, ...)). With ` +
      `a 2px floor on this panel 17 of the 24 bars were invisible. got: ${bars[0].trim()}`
  );

  // The axis is what turns 24 columns into "a day": the same class and the same
  // hour strings as the sidebar's, so the app has one vocabulary for an hour.
  const wired = html.match(
    /<div class="sparkline">\$\{insSpark\}<\/div>\s*<div class="chart-x-axis">(?:<span>[^<]*<\/span>)+<\/div>/
  );
  assert.ok(
    wired,
    `${label}: the sparkline must be followed by a .chart-x-axis row of hour labels. ` +
      'Without it the 24 columns are unlabelled and unreadable.'
  );
  for (const axisLabel of ['12 AM', '6 AM', '12 PM', '6 PM', '11 PM']) {
    assert.ok(
      wired[0].includes(`<span>${axisLabel}</span>`),
      `${label}: the sparkline axis must carry the "${axisLabel}" tick, like the ` +
        `sidebar's hour axis. got: ${wired[0]}`
    );
  }
  // The empty case still has to say so, and sparklineHasData still decides it.
  assert.ok(
    /sparklineHasData\(insBuckets\)/.test(panelFn),
    `${label}: the "no activity yet" line must stay keyed off sparklineHasData()`
  );
}

// ---- the shipped document passes -------------------------------------------
check(src, 'index.html');

// ---- anchors check() deliberately does not ---------------------------------
// The helpers, on the shipped document, so a failure names the anchor rather
// than a check number. None of these overlap check() or the two neighbouring
// contracts (tests/test_trends_legibility.js, tests/test_status_panel_reachability.js).
assert.ok(hasId('date-list') && hasId('stats-label'),
  'the sidebar list and the header count keep their ids (renderSidebar / the ' +
    'grid counter both write into them)');
// The pinned child really is the Saved disclosure, i.e. a control, not a spacer.
const asideTail = markup().match(/<aside>[\s\S]*?<\/aside>/)[0].trimEnd();
assert.ok(
  /<summary><h3><i data-lucide="bookmark"><\/i> Saved<\/h3><\/summary>/.test(
    asideTail.slice(-400)
  ),
  'the last child of <aside> must still be the Saved disclosure -- margin-top: ' +
    'auto pins whatever is last, so a new trailing node would silently move'
);
// .sparkline is declared exactly once: a second rule could put the hairlines
// back, and ruleN() naming that is more useful than a silent override.
assert.ok(/border-bottom/.test(ruleN('.sparkline', 0)), '.sparkline is rule 0 of 1');
assert.throws(() => ruleN('.sparkline', 1), /rule #1/,
  'a second .sparkline rule would let a later declaration undo the baseline');
// The row the bars stand in has an explicit height, so a bar's inline height is
// measured against something rather than against the line box.
assert.strictEqual(decl(rule('.sparkline'), 'height'), '32px',
  '.sparkline keeps its explicit 32px height -- the bars are sized in px, so a ' +
    'height-less row would render them at full line height instead of scaling');
// Whole-sheet sweep for the cap, not just the one rule body check() reads.
assert.ok(
  !/max-width:\s*14ch/.test(css()),
  'the 14ch cap on the count is gone from the sheet, not merely overridden'
);
// The "This view" block the sparkline lives in is still the one being rendered.
assert.ok(/title="computed from the frames loaded in this view/.test(
  grabFn('renderSystemPanel')
), 'renderSystemPanel still exists and still carries the "This view" aggregates');
// The count still hides below 960px. tests/test_mobile_header_chrome.js owns that
// breakpoint; this suite only owns the widths at which the count is shown.
assert.ok(
  /display:\s*none/.test(
    mediaBodies(/max-width:\s*960px/).find((b) => b.includes('#stats-label'))
  ),
  'the count must still hide below 960px, where there is no room for it at all'
);
assert.ok(loadSentinel('gridStatsLabel').gridStatsLabel.trim(),
  'the gridStatsLabel sentinel body is still non-empty (test_sentinel_inventory ' +
    'checks the markers; this checks there is still a helper in them to group with)');

// ---- every assertion above can actually fail ------------------------------
// The three headline fixes first, as explicit assert.throws with the reason each
// one has to fail spelled out (the style of
// tests/test_status_panel_reachability.js), so the evidence for the biggest
// fixes is readable without counting loop output. The rest are swept by name
// below.
assert.throws(
  () => check(src.replace(
    /#stats-label\.stats-info \{\s*min-width: 0;/,
    '#stats-label.stats-info { max-width: 14ch; overflow: hidden; ' +
      'text-overflow: ellipsis; white-space: nowrap; min-width: 0;'
  ), 'count-regression'),
  /must not use/,
  're-capping the count at 14ch must fail the readable-count check -- that cap ' +
    'is what rendered "Showing 10 of ..."'
);
assert.throws(
  () => check(src
    .replace(
      /formattedTime: timeStampLabel\(dateObj, DISPLAY_TZ, true\),/,
      "formattedTime: dateObj.toLocaleTimeString('en-AU', " +
        "{ hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: DISPLAY_TZ }),"
    )
    .replace(/const stamp = [\s\S]*?\/\/ === \/pure:timeStampLabel ===/, ''),
  'clock-regression'),
  /en-AU toLocaleTimeString clock is left/,
  'going back to the locale formatter must fail: the hour cycle has to be stated, ' +
    'or midnight loses its day period on any build that resolves the cycle to 24h'
);
assert.throws(
  () => check(src.replace(
    /aside > :last-child \{ margin-top: auto; \}/,
    'aside > :last-child { /* unpinned */ }'
  ), 'sidebar-regression'),
  /margin-top: auto/,
  'unpinning the trailing sidebar section must fail -- that pin is the whole ' +
    'difference between a framed panel and 484px of void under a 32px strip'
);

const MUTANTS = {
  're-cap the header count at 14ch': src.replace(
    /#stats-label\.stats-info \{\s*min-width: 0;/,
    '#stats-label.stats-info { max-width: 14ch; overflow: hidden; ' +
      'text-overflow: ellipsis; white-space: nowrap; min-width: 0;'
  ),
  'unpin the trailing sidebar section': src.replace(    /aside > :last-child \{ margin-top: auto; \}/,
    'aside > :last-child { /* unpinned */ }'
  ),
  'stop giving the count its own row': src.replace(
    /#stats-label\.stats-info \{ flex: 1 1 100%; text-align: left; \}/,
    '#stats-label.stats-info { text-align: left; }'
  ),
  'stop letting the tab cluster wrap': src.replace(
    /\.header-content \.tab-cluster \{ flex-wrap: wrap; row-gap: 2px; \}/,
    '.header-content .tab-cluster { row-gap: 2px; }'
  ),
  'un-cap the date list again': src.replace(
    /\.date-list \{([\s\S]*?)max-height: none;/,
    '.date-list {$1max-height: 280px;'
  ),
  'rebuild the sparkline as hairlines': src.replace(
    /flex:1 1 0;min-width:0;height:\$\{Math\.max\(4, Math\.round\(\(c \/ insMax\) \* 28\)\)\}px/,
    'display:inline-block;width:3px;margin-right:1px;height:${Math.round(2 + (c / insMax) * 16)}px'
  ),
  'drop the sparkline baseline': src.replace(
    /(\.sparkline \{[\s\S]*?)border-bottom: 1px solid var\(--border-color\);/,
    '$1border-bottom: none;'
  ),
  'drop the sparkline axis': src.replace(
    /\n\s*<div class="chart-x-axis"><span>12 AM<\/span><span>6 AM<\/span><span>12 PM<\/span><span>6 PM<\/span><span>11 PM<\/span><\/div>/,
    ''
  ),
  'fold midnight into the afternoon': src.replace(
    "const meridiem = h < 12 ? 'AM' : 'PM';",
    "const meridiem = 'PM';"
  ),
  'map hour 0 to 0 again': src.replace(
    'String(h % 12 === 0 ? 12 : h % 12)',
    'String(h % 12)'
  ),
  'go back to the locale formatter': src
    .replace(
      /formattedTime: timeStampLabel\(dateObj, DISPLAY_TZ, true\),/,
      "formattedTime: dateObj.toLocaleTimeString('en-AU', " +
        "{ hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: DISPLAY_TZ }),"
    )
    .replace(/const stamp = [\s\S]*?\/\/ === \/pure:timeStampLabel ===/, ''),
};

let killed = 0;
for (const [name, mutant] of Object.entries(MUTANTS)) {
  assert.notStrictEqual(mutant, src, `mutation "${name}" did not change the source`);
  let failure = null;
  try {
    check(mutant, name);
  } catch (e) {
    failure = e;
  }
  assert.ok(
    failure,
    `mutant "${name}" SURVIVED -- check() accepted the pre-fix document, so the ` +
      'assertions above cannot fail and prove nothing'
  );
  console.log(`KILLED  ${name}  ::  ${String(failure.message).split('\n')[0].slice(0, 100)}`);
  killed++;
}
assert.strictEqual(killed, Object.keys(MUTANTS).length, 'every mutant must be caught');

console.log(`ui polish regressions: all assertions passed (${killed} mutants killed)`);
