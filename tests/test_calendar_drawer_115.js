// #115 archive drawer + #119 keyboard contract, asserted on index.html as
// text (the Node suites read the page; tools/screenshots drive the browser).
const assert = require('assert');
const { src: html, grabFn, mediaBodies } = require('./helpers/load.cjs');

// ---- 1. the rail is the drawer, named and controlled -----------------------
assert.match(html, /<aside id="archive-sidebar">/,
  'the rail aside must carry id="archive-sidebar" -- that is the id the toggle ' +
  'controls and the drawer script turns into a dialog');
const toggle = html.match(/<button[^>]*id="sidebar-toggle"[^>]*>/);
assert.ok(toggle, 'the #sidebar-toggle button is gone from the header');
assert.match(toggle[0], /aria-controls="archive-sidebar"/,
  '#sidebar-toggle must point aria-controls at the drawer');
assert.match(toggle[0], /aria-expanded="(?:true|false)"/,
  '#sidebar-toggle must state its state with aria-expanded');
assert.doesNotMatch(toggle[0], /aria-pressed/,
  'aria-pressed read "true" while the rail was collapsed, i.e. the opposite of ' +
  'aria-expanded -- one state, one attribute (#119)');

// ---- 2. the phone drawer: fixed, and every control 44px --------------------
const phone = mediaBodies(/max-width:\s*768px/).join('\n');
assert.ok(
  /\.workspace:not\(\.sidebar-collapsed\)(?::not\(\.mode-dashboard\))? aside\s*\{[^}]*position:\s*fixed/.test(phone),
  'the expanded rail must be position: fixed below 768px, or opening the drawer ' +
  'pushes the gallery ~1100px down a phone viewport'
);
for (const sel of ['.fv-cal-all', '.fv-cal-arrow', '.fv-cal-month', '.fv-cal-day']) {
  assert.ok(
    new RegExp(sel.replace('.', '\\.') + '\\s*\\{[^}]*44px').test(phone),
    sel + ' must reach 44px in the phone block -- WCAG 2.5.5/2.5.8 target size'
  );
}

// ---- 3. the scrim and the drawer's own close control ----------------------
assert.match(html, /<div id="sidebar-scrim" hidden><\/div>/,
  'the phone drawer needs its #sidebar-scrim backdrop');
assert.match(html, /<button type="button" class="fv-cal-close" id="calendar-close"/,
  'the drawer needs a visible #calendar-close control');

// ---- 4. the drawer is a modal dialog, trapped, Escape-closable -------------
const drawer = html.match(
  /const asideEl = document\.querySelector\('\.workspace aside'\);[\s\S]*?window\.closeCalendarDrawer = \(\) => \{ if \(drawerOpen\(\)\) setSidebarCollapsed\(true\); \};/
);
assert.ok(drawer, 'the drawer script that owns .workspace aside is gone');
assert.match(drawer[0], /asideEl\.setAttribute\('role', 'dialog'\)/,
  'the expanded rail must become role="dialog" on a phone');
assert.match(drawer[0], /asideEl\.setAttribute\('aria-modal', 'true'\)/,
  'a full-screen drawer must be aria-modal, so the page behind is inert');
assert.match(drawer[0], /trapFocus\(asideEl, moveFocus\)/,
  'the drawer must use trapFocus -- a dialog without a focus trap lets Tab walk ' +
  'the gallery behind the overlay, and focus never returns to the toggle');
assert.match(drawer[0], /document\.addEventListener\('keydown', onDrawerKeydown, true\)/,
  'the drawer must register its keydown handler on document (capture), so ' +
  'Escape still works after focus falls back to the body');
assert.match(drawer[0], /e\.key !== 'Escape'/,
  'Escape must close the drawer');
assert.match(drawer[0], /window\.closeCalendarDrawer = \(\) => \{ if \(drawerOpen\(\)\) setSidebarCollapsed\(true\); \}/,
  'closeCalendarDrawer must exist as a no-op-when-closed window helper');

// ---- 5. #119: the rebuilt calendar keeps keyboard focus -------------------
const fn = grabFn('renderDateSidebar');
assert.match(fn, /document\.activeElement\.dataset\.calFocus/,
  'renderDateSidebar must capture which control had focus before the rebuild');
assert.match(fn, /allItem\.dataset\.calFocus = 'all'/, 'All dates needs a focus key');
assert.match(fn, /prev\.dataset\.calFocus = 'prev'/, 'previous month needs a focus key');
assert.match(fn, /choose\.dataset\.calFocus = 'month'/, 'the month picker needs a focus key');
assert.match(fn, /next\.dataset\.calFocus = 'next'/, 'next month needs a focus key');
assert.match(fn, /button\.dataset\.calFocus = 'day-' \+ day/,
  'every day button needs a focus key, disabled ones included');
assert.match(fn, /if \(focusKey\) \{[\s\S]*?t\.focus\(\{ preventScroll: true \}\);/,
  'the captured focus key must be restored after the rebuild, without scrolling');

// ---- 6. choosing a date closes the drawer; browsing does not --------------
assert.match(fn,
  /state\.activeDateFilter = 'all';[\s\S]*?applyFiltersAndSearch\(\);\s*\n\s*if \(window\.closeCalendarDrawer\) window\.closeCalendarDrawer\(\);/,
  'the All dates handler must call closeCalendarDrawer after applyFiltersAndSearch()');
assert.match(fn,
  /state\.activeDateFilter = dateStr;[\s\S]*?applyFiltersAndSearch\(\);\s*\n\s*if \(window\.closeCalendarDrawer\) window\.closeCalendarDrawer\(\);/,
  'the day handler must call closeCalendarDrawer after applyFiltersAndSearch()');
for (const [who, re] of [
  ['previous month', /prev\.addEventListener\('click', \(\) => \{[\s\S]*?\}\);/],
  ['next month', /next\.addEventListener\('click', \(\) => \{[\s\S]*?\}\);/],
  ['the month picker', /choose\.addEventListener\('change', \(\) => \{[\s\S]*?\}\);/],
]) {
  const handler = fn.match(re);
  assert.ok(handler, 'renderDateSidebar lost the ' + who + ' handler');
  assert.doesNotMatch(handler[0], /closeCalendarDrawer/,
    'browsing months must not close the drawer -- only choosing a date does');
}

console.log('calendar-drawer-115: all assertions passed');
