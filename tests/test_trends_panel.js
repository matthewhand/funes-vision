// Contract: the trends charts live in the content area, not in the navigation
// rail, so they exist at every width.
//
// MEASURED BUG (headless Chromium, synthetic fixtures, tools/screenshots/proxy.py,
// page loaded AT each width so the sidebar's initial state is the shipped one):
//
//  1. ABSENT ON PHONES. 320/360/390/414/768px:
//       <aside> height 0px, computed display:none  (sidebar-collapsed is the
//       DEFAULT below the 992px breakpoint -- sidebarDefaultCollapsed()), and
//       every .chart-hit measured 0x0. The app's only trends surface was
//       desktop-only; the Motion tab on a phone was a photo grid with no chart
//       anywhere in the document.
//
//  2. WRONG PLACE. 1440x1000, six days in the archive:
//       <aside> 196px wide; .chart-viewport.chart-days 196px; six day bars at
//       28.8px each; the 24 hour bars at 5px each. 576px of 24px WCAG 2.5.8
//       targets in a 196px column, so the last one hung 14.9px past the panel's
//       content edge and made the rail a 15px horizontal scroller
//       (aside scrollWidth 211 vs clientWidth 196) -- which is what put the
//       "11 PM" axis label on the boundary of a clipped strip.
//
// AFTER: the same measurement gives <aside> 0px (still, correctly, collapsed on
// a phone) but the panel is VISIBLE at all eight widths, the day planner is
// 91.3px per bar at 1440 (571px viewport / 6) and 51.5px at 390, adjacent bars
// keep a 2px gap at every width and in a 34-day archive, and "11 PM" ends 1.0px
// inside the panel's content box with no scrolling ancestor in the way.
//
// This file owns LOCATION and REACHABILITY. tests/test_trends_legibility.js
// owns the disclosure shipping open + the day axis, and
// tests/test_chart_target_size.js owns the 24x24 floors, so neither is repeated
// here. These are static assertions -- they pin the recipe, not the layout. The
// browser measurements quoted above are the layout proof; the mutation block at
// the bottom re-runs every check against a pre-move copy of the document to
// prove each one can actually fail.
const assert = require('assert');
const { src, createLoader } = require('./helpers/load.cjs');

// The width at or below which the shipped default collapses the rail. It comes
// from sidebarDefaultCollapsed()'s default breakpoint, so the two must not drift.
const RAIL_COLLAPSE = 992;
// The phone breakpoint the panel's initial view keys off.
const PHONE = /max-width:\s*768px/;

function check(html, label) {
  const L = createLoader(html, label);
  // Every lookup goes through L. The module-level exports are bound to the
  // SHIPPED document, so a bare markup() here would read index.html and pass
  // every mutant below -- the exact vacuous-check failure these helpers exist
  // to prevent.
  const body = L.markup();

  // ---- 1. the host is in the content column, not in the rail ----------------
  const aside = body.match(/<aside(?:\s[^>]*)?>[\s\S]*?<\/aside>/);
  assert.ok(aside, `${label}: no <aside> to check the rail against`);
  assert.ok(
    !/activity-chart-container/.test(aside[0]),
    `${label}: the chart host is back inside <aside> -- the rail is ` +
      'display:none below ' + RAIL_COLLAPSE + 'px by default (sidebarDefaultCollapsed), ' +
      'so a chart in it does not exist on a phone at all. Measured: aside height 0px ' +
      'and every .chart-hit 0x0 at 320/360/390/414/768.'
  );

  const content = body.match(/<div class="content-area">[\s\S]*$/);
  assert.ok(content, `${label}: no .content-area`);
  const host = content[0].match(
    /<details class="([^"]*\bactivity-chart-container\b[^"]*)"([^>]*)>/
  );
  assert.ok(
    host,
    `${label}: the chart host must be a <details> inside .content-area, above the ` +
      'feed. It is the only trends surface, and renderActivityChart() bails while ' +
      'the host is closed, so a host that is not in the feed column is a host a ' +
      'phone never renders.'
  );

  // The panel has to be ABOVE the grid/timeline feed, not appended below it.
  const grid = content[0].indexOf('id="image-grid"');
  assert.ok(grid > -1, `${label}: .content-area must still contain the image grid`);
  assert.ok(
    content[0].indexOf('<details class="') < grid,
    `${label}: the trends panel must come before #image-grid. Below the feed it is ` +
      'off-screen on a phone, which is the same defect as not shipping it at all.'
  );

  // The rail's own disclosure set: Dates + Saved only. A second copy of the
  // chart in the rail is the failure this whole move exists to prevent.
  assert.ok(
    !/id="activity-chart(-days)?"/.test(aside[0]),
    `${label}: the rail still holds a chart container. Two copies of the same ` +
      'chart means two sources of truth for one number.'
  );
  assert.ok(
    /data-lucide="bookmark"/.test(aside[0]) && /data-lucide="calendar"/.test(aside[0]),
    `${label}: the rail must still hold its Dates and Saved sections`
  );
  // The rail keeps the panel's toggle out of itself.
  assert.ok(
    !/trend-view-btn/.test(aside[0]),
    `${label}: the view toggle belongs in the panel, not in the rail`
  );

  // ---- 2. the rail's collapse cannot hide the panel ------------------------
  // The one rule that made the chart vanish. If it ever grows a selector that
  // reaches .content-area, the phone regression returns silently.
  // Selector, not body: rule() splits a comma list and hands back each part's
  // body, so `aside, .activity-chart-container { display:none }` would read here
  // as the same `display:none` on `aside` alone.
  // #115: the drawer's own rule, `.workspace:not(.sidebar-collapsed) aside`, is
  // the rail's EXPANDED state on a phone (a fixed bottom sheet that must stay
  // visible), not a collapse rule, so it is the one `.sidebar-collapsed`
  // selector that is exempt from the hide requirement.
  const collapseRules = L.cssRules().filter((r) =>
    /\.sidebar-collapsed/.test(r.selector) && !/:not\(\.sidebar-collapsed\)/.test(r.selector));
  assert.ok(
    collapseRules.length,
    `${label}: the rule that collapses the rail on small screens is gone, so the ` +
      'rail and the feed would share one column on a phone'
  );
  for (const r of collapseRules) {
    assert.ok(
      /display:\s*none/.test(r.body),
      `${label}: "${r.selector}" must keep hiding the rail. got: ${r.body.trim()}`
    );
    assert.ok(
      !/trends|activity-chart|content-area/.test(r.selector),
      `${label}: the sidebar-collapse rule now reads "${r.selector}" -- collapsing ` +
        'the rail may not collapse the charts. That is the 0px-aside regression.'
    );
  }
  // And the panel must not be inside anything else that hides on small screens.
  const stacked = L.mediaBodies(/max-width:\s*992px/);
  for (const body2 of stacked) {
    const rules = [...body2.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
      .filter((m) => /display:\s*none/.test(m[2]))
      .map((m) => m[1].trim());
    for (const sel of rules) {
      assert.ok(
        !/trends|activity-chart/.test(sel),
        `${label}: the <=992px block hides "${sel}" -- that is how the chart ` +
          'disappeared from phones. Only the rail may be display:none there.'
      );
    }
  }
  // Nothing in the panel's own chain may clip or scroll horizontally either:
  // "11 PM" sat on the boundary of a 15px scroller in the rail (aside
  // scrollWidth 211 vs clientWidth 196, overflow-x computed to auto from
  // `overflow-y: auto`).
  for (const r of L.cssRules()) {
    if (!/(^|[\s,])\.trends-(panel|views|view)\b/.test(r.selector)) continue;
    assert.ok(
      !/overflow-x/.test(r.body),
      `${label}: "${r.selector}" sets overflow-x. The rail became a horizontal ` +
        'scroller for the 24px chart targets (scrollWidth 211 vs clientWidth 196) ' +
        'and that is what clipped the last axis label. got: ' +
        r.body.trim().replace(/\s+/g, ' ')
    );
  }

  // ---- 3. the panel is a card, not a new palette ---------------------------
  // .panel is the file's own unused surface primitive (--bg-secondary, 1px
  // --border-color, --radius-3, --space-4). Reusing it is the point: a panel
  // that invents its own background is a second design system.
  assert.ok(
    /class="panel trends-panel activity-chart-container"/.test(html),
    `${label}: the panel must reuse the .panel primitive as its surface`
  );
  const surface = L.rule('.panel');
  assert.ok(
    /background:\s*var\(--bg-secondary\)/.test(surface) &&
      /border-color\)/.test(surface) &&
      /var\(--radius-3\)/.test(surface),
    `${label}: .panel must still be the token surface the trends panel inherits. ` +
      `got: ${surface.trim().replace(/\s+/g, ' ')}`
  );
  // The panel's own declarations may only reference tokens that already exist.
  const known = new Set([...L.css().matchAll(/(--[a-z0-9-]+)\s*:/g)].map((m) => m[1]));
  for (const sel of ['.trends-panel h3', '.trends-summary-hint']) {
    for (const m of L.rule(sel).matchAll(/var\((--[a-z0-9-]+)/g)) {
      assert.ok(
        known.has(m[1]),
        `${label}: ${sel} uses var(${m[1]}), which no rule in the sheet declares`
      );
    }
  }

  // ---- 4. the view toggle --------------------------------------------------
  // A phone is not made to scroll two charts, and a desktop user can focus one.
  for (const v of ['days', 'hours', 'both']) {
    assert.ok(
      new RegExp(`class="trend-view-btn[^"]*" data-trend="${v}"`).test(html),
      `${label}: the view toggle is missing its "${v}" button`
    );
  }
  assert.ok(
    /aria-pressed="true">Both</.test(html) && /aria-pressed="false">By day</.test(html),
    `${label}: each toggle button needs an aria-pressed matching its own state`
  );
  assert.ok(
    /class="segmented trends-toggle" role="group" aria-label="Trend chart view"/.test(html),
    `${label}: the toggle must be the .segmented primitive in a labelled group`
  );
  // The buttons are 32px tall / 32px min-width via .segmented > button, which is
  // the same floor the rest of the header chrome clears. Stated here because the
  // a11y gate measures it, not this file.
  const seg = L.rule('.segmented > button');
  assert.ok(
    /height:\s*var\(--control-h-sm\)/.test(seg) && /min-width:\s*var\(--control-h-sm\)/.test(seg),
    `${label}: .segmented > button must keep its 32px control floor, or the new ` +
      `toggle adds sub-24px targets to the page. got: ${seg.trim().replace(/\s+/g, ' ')}`
  );
  // .trends-view is display:flex, which outranks the UA [hidden] rule, so the
  // hidden half of the toggle has to be stated or both charts stay painted.
  const hidden = L.rule('.trends-view[hidden]');
  assert.ok(
    /display:\s*none/.test(hidden),
    `${label}: .trends-view[hidden] must set display:none -- .trends-view is ` +
      `display:flex and a class beats the UA [hidden] rule. got: ${hidden.trim()}`
  );
  const setTrend = L.grabFn('setTrendView');
  assert.ok(
    /trendViews\[k\]\.hidden = view !== 'both' && view !== k/.test(setTrend),
    `${label}: setTrendView must hide the views it is not showing`
  );
  assert.ok(
    /b\.setAttribute\('aria-pressed', String\(on\)\)/.test(setTrend),
    `${label}: setTrendView must move aria-pressed with the active class, or the ` +
      'announced state and the painted state disagree'
  );
  // A phone starts on one chart. Two stacked charts plus the header leave the
  // viewport-capped feed (syncGridOffset) ~256px tall at 320x900, measured.
  assert.ok(
    /setTrendView\(window\.matchMedia\('\(max-width: 768px\)'\)\.matches \? 'days' : 'both'\)/.test(html),
    `${label}: the initial view must be phone-dependent -- "Both" everywhere left ` +
      'the feed 256px tall at 320px (measured) and made a phone scroll two charts'
  );
  assert.ok(
    L.mediaBodies(PHONE).length > 0,
    `${label}: the file lost its ${PHONE} block, so the phone layout is unpinned`
  );
  // "Both" only shares a row when a half-width day planner is still a day
  // planner, and only when both are showing.
  const twoUp = L.mediaBodies(/min-width:\s*900px/).find((b) => b.includes('.trends-views'));
  assert.ok(
    twoUp && /grid-template-columns:\s*1fr 1fr/.test(twoUp) &&
      /:not\(:has\(\.trends-view\[hidden\]\)\)/.test(twoUp),
    `${label}: the two-up row must be width-gated and must collapse to one column ` +
      'whenever a single view is showing. got: ' + String(twoUp && twoUp.trim())
  );
  // Folding the panel, or swapping which chart it shows, moves the feed -- and
  // the feed's cap is measured from its own top (syncGridOffset). Both paths
  // have to re-measure, so each is pinned where it is written.
  assert.ok(
    /setTrendView\(b\.dataset\.trend\);\n        if \(window\.syncGridOffset\) window\.syncGridOffset\(\);/.test(html),
    `${label}: the view toggle must re-run syncGridOffset -- the panel sits above a ` +
      'grid capped to the viewport, so a height change there is a stale cap'
  );
  assert.ok(
    /chartPanel\.addEventListener\('toggle',[\s\S]*?window\.syncGridOffset/.test(html),
    `${label}: the disclosure toggle must re-run syncGridOffset too -- folding the ` +
      'panel moves the feed, and the cap is measured from the grid top'
  );

  // ---- 5. both chart containers, and their axis, are still here ------------
  // Relocating markup is how a chart silently loses its data hook.
  for (const id of ['activity-chart', 'activity-chart-days', 'chart-x-axis-days']) {
    assert.ok(
      L.hasId(id),
      `${label}: #${id} moved or was renamed -- renderActivityChart() and ` +
        'clearActivityChart() both address it by id'
    );
  }
  const clear = L.grabFn('clearActivityChart');
  assert.ok(
    /'activity-chart',\s*'activity-chart-days',\s*'chart-x-axis-days'/.test(clear),
    `${label}: clearActivityChart() must still empty all three containers, or a ` +
      'filtered-out day keeps its old bar'
  );
  // Each container is inside the view its toggle button controls, so "By day"
  // cannot leave the hour histogram on screen.
  for (const [v, id] of [['days', 'activity-chart-days'], ['hours', 'activity-chart']]) {
    const block = content[0].match(
      new RegExp(`data-trend-view="${v}">[\\s\\S]*?</div>\\s*</div>`)
    );
    assert.ok(
      block && block[0].includes(`id="${id}"`),
      `${label}: #${id} must sit inside the "${v}" view the toggle shows and hides`
    );
  }
  // The hour axis is five spans and the last one is 11 PM; the trends one is
  // multi-line on purpose, because tests/test_ui_polish_regressions.js splices
  // the SPARKLINE's single-line axis and must not find this one first.
  const hourBlock = content[0].match(/data-trend-view="hours">[\s\S]*?<\/details>/);
  assert.ok(hourBlock, `${label}: no hours view`);
  for (const tick of ['12 AM', '6 AM', '12 PM', '6 PM', '11 PM']) {
    assert.ok(
      hourBlock[0].includes(`<span>${tick}</span>`),
      `${label}: the trends hour axis is missing the "${tick}" tick`
    );
  }
  assert.ok(
    /<div class="chart-x-axis">\n\s*<span>12 AM<\/span>/.test(hourBlock[0]),
    `${label}: the trends hour axis must stay one <span> per line. A single-line ` +
      'axis makes test_ui_polish_regressions.js "drop the sparkline axis" mutant ' +
      'splice THIS axis, leaving the sparkline it is meant to cover unverified'
  );

  // ---- 6. the rail's own disclosures still behave --------------------------
  // margin-top:auto pins whatever is last, so removing a section silently moved
  // the pin (tests/test_ui_polish_regressions.js owns the declaration; this
  // owns that the thing it now pins is the Saved row, not the charts).
  assert.ok(
    /<summary><h3><i data-lucide="bookmark"><\/i> Saved<\/h3><\/summary>/.test(
      aside[0].slice(-400).trimEnd()
    ),
    `${label}: the rail's last child must still be the Saved disclosure -- the ` +
      'trailing margin-top:auto pin follows it, and the charts used to be the ' +
      'section before it'
  );
}

// ---- the shipped document passes -------------------------------------------
check(src, 'index.html');

// ---- every assertion above can actually fail ------------------------------
// Moving the host back into the rail is the regression this contract exists for,
// so it is proved first and named.
const backInRail = src.replace(
  /<div class="content-area">\n        <!-- Trends[\s\S]*?<\/details>\n/,
  '      <div class="content-area">\n'
).replace(
  /(        <details class="sidebar-section">\n          <summary><h3><i data-lucide="bookmark">)/,
  '        <details class="sidebar-section activity-chart-container" open>\n' +
    '          <summary><h3><i data-lucide="activity"></i> Motion</h3></summary>\n' +
    '        </details>\n\n$1'
);
assert.notStrictEqual(backInRail, src, 'the "put the charts back in the rail" mutation did nothing');
assert.throws(
  () => check(backInRail, 'charts-back-in-the-rail'),
  /is back inside <aside>/,
  'putting the chart host back in the rail must fail: that is the 0px-aside, ' +
    '0x0-hit, no-trends-on-a-phone regression'
);

const belowTheFeed = src.replace(
  /        <!-- Trends:[\s\S]*?<\/details>\n/,
  ''
).replace(
  /(          <div id="scroll-sentinel" class="scroll-sentinel">)/,
  '          <details class="panel trends-panel activity-chart-container" open></details>\n$1'
);
assert.notStrictEqual(
  belowTheFeed, src, 'the "move the panel below the feed" mutation did nothing'
);
assert.throws(
  () => check(belowTheFeed, 'panel-below-the-feed'),
  /must come before #image-grid/,
  'a panel below #image-grid is off-screen on a phone, which is the same defect ' +
    'as not shipping it at all'
);

const MUTANTS = {
  'let the rail-collapse rule reach the panel': src.replace(
    '.workspace.sidebar-collapsed aside { display: none; }',
    '.workspace.sidebar-collapsed aside, .workspace.sidebar-collapsed ' +
      '.activity-chart-container { display: none; }'
  ),
  'let the <=992px block hide the panel': src.replace(
    /(\n      aside > \* \{ min-width: 0; max-width: 100%; \})/,
    '$1\n      .activity-chart-container { display: none; }'
  ),
  're-clip the panel horizontally': src.replace(
    '.trends-view {\n      display: flex;',
    '.trends-view {\n      overflow-x: auto;\n      display: flex;'
  ),
  'drop the toggle entirely': src.replace(
    /<div class="trends-toolbar">[\s\S]*?<\/div>\n          <\/div>\n          <div class="trends-views">/,
    '          <div class="trends-views">'
  ),
  'leave the hidden view painted': src.replace(
    '.trends-view[hidden] { display: none; }',
    '.trends-view[hidden] { }'
  ),
  'drop aria-pressed from the toggle': src.replace(
    /b\.setAttribute\('aria-pressed', String\(on\)\);/,
    ''
  ),
  'show Both on a phone': src.replace(
    "matchMedia('(max-width: 768px)').matches ? 'days' : 'both'",
    "'both'"
  ),
  'two charts side by side at every width': src.replace(
    /@media \(min-width: 900px\) \{\n      \.trends-views:not\(:has\(\.trends-view\[hidden\]\)\) \{ grid-template-columns: 1fr 1fr; \}\n    \}\n/,
    '.trends-views { grid-template-columns: 1fr 1fr; }\n'
  ),
  'stop re-measuring the grid cap': src.replace(
    /setTrendView\(b\.dataset\.trend\);\n        if \(window\.syncGridOffset\) window\.syncGridOffset\(\);/,
    'setTrendView(b.dataset.trend);'
  ),
  'orphan the day container from its view': src.replace(
    /data-trend-view="days">/,
    'data-trend-view="hourly">'
  ),
  'invent a palette for the panel': src.replace(
    'background: var(--bg-secondary);\n      border: 1px solid var(--border-color);\n      border-radius: var(--radius-3);',
    'background: #101820;\n      border: 1px solid #24313f;\n      border-radius: 10px;'
  ),
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
    `mutant "${name}" SURVIVED -- check() accepted it, so the assertions above ` +
      'cannot fail and prove nothing'
  );
  console.log(`KILLED  ${name}  ::  ${String(failure.message).split('\n')[0].slice(0, 96)}`);
  killed++;
}
assert.strictEqual(killed, Object.keys(MUTANTS).length, 'every mutant must be caught');

console.log(`trends panel: all assertions passed (${killed} mutants killed)`);
