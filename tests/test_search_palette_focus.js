// Contract test for the search palette's overlay behaviour (issue #76).
//
// Two defects, both confirmed in a real headless browser:
//
//  1. #search-popup carries aria-modal="true" but openSearchPopup never called
//     trapFocus(), so Tab walked the whole page BEHIND the overlay
//     (body -> #sidebar-toggle -> #btn-switch-feed -> the #filter-tabs buttons)
//     while assistive tech was told the background was inert. Only the Escape
//     path restored focus, so a backdrop close left activeElement on <body>.
//  2. openSearchPopup had no mutual exclusion with the setPopoverOpen popovers.
//     With the Filters popover open, Ctrl+K opened the palette on top, but the
//     first click into #search-input was swallowed by that popover's document
//     outside-click handler, whose trapFocus release pulled focus back to
//     #btn-filters — so every keystroke went nowhere and state.searchQuery
//     stayed empty while the grid kept its old filters.
//
// Run: node tests/test_search_palette_focus.js
const assert = require('assert');
const { src: html, grabFn } = require('./helpers/load.cjs');

const open = grabFn('openSearchPopup');
const close = grabFn('closeSearchPopup');

// ---------------------------------------------------------------- 1. the trap
assert.ok(/searchPopupRelease = trapFocus\(searchPopup\)/.test(open),
  'openSearchPopup must call trapFocus(searchPopup) — aria-modal="true" without a focus ' +
  'trap lets Tab walk the whole page behind the palette (issue #76)');
assert.ok(!/setTimeout\(\(\) => searchPopupInput\.focus\(\)/.test(open),
  "the old focus timeout must be gone; trapFocus focuses the palette's first focusable");

assert.ok(/if \(searchPopupRelease\) \{ searchPopupRelease\(\); searchPopupRelease = null; \}/.test(close),
  'closeSearchPopup must release the trap, so EVERY close path (backdrop, Escape, ' +
  'Ctrl+K toggle) restores focus — not just Escape');
assert.ok(/document\.activeElement === document\.body[\s\S]*?searchTrigger \|\| searchPopupInput\)\.focus\(\)/.test(close),
  'when nothing claimed focus on release, closeSearchPopup must land it on the trigger ' +
  'instead of leaving it on <body>');

const kdAt = html.indexOf("document.addEventListener('keydown', (e) => {\n        if ((e.metaKey");
assert.notStrictEqual(kdAt, -1, 'the Ctrl+K keydown handler not found');
const keydown = html.slice(kdAt, kdAt + 900);
assert.ok(!/if \(e\.key === 'Escape' && searchPopup\.classList\.contains\('open'\)\) \{[\s\S]*?searchTrigger && searchTrigger\.focus\(\)/.test(keydown),
  'the manual Escape focus restore is superseded by the trapFocus release');

// ------------------------------------------------- 2. popover mutual exclusion
assert.ok(/closeAllPopovers\(false\)/.test(open),
  "openSearchPopup must close every setPopoverOpen popover first, or the first click into " +
  "the palette is stolen by that popover's document outside-click handler (#76)");

const closeAll = grabFn('closeAllPopovers');
const table = html.match(/const POPOVERS = \[([\s\S]*?)\];/);
assert(table, 'POPOVERS table not found');
const menuIds = [...table[1].matchAll(/'([a-z-]+)',\s*'([a-z-]+)',\s*'([a-z-]+)'/g)].map(m => m[2]);
for (const menuId of ['blacklist-dropdown', 'filters-dropdown', 'integrations-dropdown', 'system-dropdown']) {
  assert.ok(menuIds.includes(menuId), 'POPOVERS must cover the ' + menuId + ' popover');
  assert.ok(html.includes(`id="${menuId}"`), menuId + ' must exist in the markup');
}
assert.ok(/POPOVERS\.forEach/.test(closeAll) && /setPopoverOpen\(id, menu, btn, false\)/.test(closeAll),
  'closeAllPopovers must close through setPopoverOpen so each popover focus trap is released');
assert.ok(/if \(btn && restoreFocus\) btn\.focus\(\)/.test(closeAll),
  'closeAllPopovers(false) from the palette must not yank focus back to a popover trigger');
assert.ok(/closeAllPopovers\(true\)/.test(html),
  'Escape must reuse the shared table (keyboard parity with click-outside)');

// --------------------------------------------- 3. measured: Tab cannot escape
// Run the REAL openSearchPopup / closeSearchPopup / trapFocus / tabWrap bodies
// against a fake DOM, so "Tab stays inside the palette" is measured, not asserted
// by reading the source.
function fakeEl(id, focusables) {
  const cls = new Set();
  const handlers = {};
  return {
    id,
    value: '',
    offsetParent: {}, // visible -> inside the trap's focusable list
    focusables: focusables || [],
    focused: 0,
    focus() { this.focused++; FAKE.activeElement = this; },
    setAttribute() {},
    addEventListener(t, fn) { handlers[t] = fn; },
    removeEventListener(t) { delete handlers[t]; },
    querySelectorAll() { return this.focusables; },
    querySelector() { return this.focusables[0] || null; },
    fire(t, e) { if (handlers[t]) handlers[t](e); return !!handlers[t]; },
    classList: {
      add: (c) => cls.add(c), remove: (c) => cls.delete(c),
      contains: (c) => cls.has(c), toggle: (c, on) => (on ? cls.add(c) : cls.delete(c)),
    },
  };
}
const FAKE = { activeElement: null };

const searchInput = fakeEl('search-input');
const hiddenClear = fakeEl('search-clear');
hiddenClear.offsetParent = null; // the × is hidden until there is text
const overlay = fakeEl('search-popup', [searchInput, hiddenClear]);
const trigger = fakeEl('search-trigger');
const pageButton = fakeEl('sidebar-toggle'); // stand-in for the page behind

const fakeDocument = {
  activeElement: null,
  body: { style: {} },
  getElementById: () => null,
  querySelectorAll: () => [],
};

const closedPopovers = [];
const factory = new Function(
  'searchPopup', 'searchPopupInput', 'searchPopupCount', 'searchTrigger',
  'closeAllPopovers', 'syncSearchPopupCount', 'document', 'modalIsOpen',
  'var searchPopupRelease = null;\n' +
  grabFn('tabWrap') + '\n' + grabFn('trapFocus') + '\n' + open + '\n' + close +
  '\nreturn { open: openSearchPopup, close: closeSearchPopup, hasRelease: function () { return !!searchPopupRelease; } };'
);
const api = factory(
  overlay, searchInput, null, trigger,
  (restoreFocus) => closedPopovers.push(restoreFocus),
  () => {}, fakeDocument, () => false
);

// Focus sits on the trigger, as if it was clicked.
fakeDocument.activeElement = trigger;
api.open();
assert.deepStrictEqual(closedPopovers, [false],
  'opening the palette must close the open popovers, and must NOT restore their trigger focus');
assert.ok(searchInput.focused >= 1, 'focus must land inside the palette (#search-input)');
assert.ok(api.hasRelease(), 'the trap must be armed');

// Tab from the only visible focusable must wrap to it, never to the page behind.
let prevented = false;
assert.ok(overlay.fire('keydown', { key: 'Tab', shiftKey: false, preventDefault() { prevented = true; } }),
  'the palette must own a keydown handler (the focus trap)');
assert.ok(prevented, 'Tab at the boundary must be intercepted, not passed to the page');
assert.strictEqual(FAKE.activeElement, searchInput, 'Tab must stay on the palette input');
assert.notStrictEqual(FAKE.activeElement, pageButton, 'focus must never reach the page behind');

assert.ok(api.hasRelease(), 'the release handle must still be armed before close');
api.close();
assert.ok(!api.hasRelease(), 'closing must release the trap exactly once');
assert.strictEqual(FAKE.activeElement, trigger, 'closing must restore focus to the opener');

// Opening must also be refused while a modal owns the keyboard (#79), so the
// two guards cannot be reasoned about independently.
const withModal = factory(
  overlay, searchInput, null, trigger,
  () => {}, () => {}, fakeDocument, () => true
);
searchInput.focused = 0;
withModal.open();
assert.strictEqual(searchInput.focused, 0, 'with a modal open the palette must not open at all (#79)');
assert.ok(!withModal.hasRelease(), 'and must not arm a focus trap');

console.log('search-palette-focus: focus trap wired + measured, popovers closed first, focus restored');
