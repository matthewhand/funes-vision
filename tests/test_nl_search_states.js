// Node tests for the natural-language search status state machine and the view
// chrome it has to reach (issue #77).
//
// Measured defects, all from a real headless browser with an intercepted
// /tools/needle-client.js:
//
//  1. #nl-search-status recorded ZERO mutations across a 2.5s in-flight query
//     and across a throwing query: syncNlStatus() ran only AFTER the await, so
//     state.nlSearch.busy / .loading were never painted, and
//     resolveNaturalLanguage() swallowed every error, making a failed query
//     look exactly like a success. (Init failure was the mirror bug: the status
//     went completely blank.)
//  2. An NL `view` result moved state.activeFilter but left the #filter-tabs
//     buttons and body[data-view] on the old view; applySavedSearch had the same
//     gap, and body[data-view] was unset on first paint at all.
//  3. An NL `time_start`/`time_end` result never reached updateSliderLabel(), so
//     #time-chip-label still read "All day", the wrapper kept range-collapsed and
//     `.toolbar-bar:has(.range-collapsed){display:none}` hid the whole time bar.
//  4. An NL `objects` result never reached renderObjectFilters(), so no chip lit
//     up and #filters-active-count stayed hidden.
//
// Run: node tests/test_nl_search_states.js
const assert = require('assert');
const { src: html, rule, grabFn, loadSentinel } = require('./helpers/load.cjs');
const sentinel = (name) => loadSentinel(name)[name];

// Same brace-walk, for `const NAME = [async ](...) => { ... }`.
function grabArrow(name) {
  const m = new RegExp('\\n([ \\t]*)const ' + name + ' = (?:async )?\\(').exec(html);
  assert(m, name + ' not found in index.html');
  const at = m.index;
  const arrow = html.indexOf('=> {', at);
  assert.notStrictEqual(arrow, -1, name + ' is not a block-bodied arrow function');
  const brace = arrow + '=> {'.length - 1;
  let depth = 0;
  for (let i = brace; i < html.length; i++) {
    if (html[i] === '{') depth++;
    else if (html[i] === '}' && --depth === 0) return html.slice(at, i + 1);
  }
  throw new Error('unbalanced braces in ' + name);
}

// =================================================== 1. the state machine
const nlStatusView = new Function(sentinel('nlStatusView') + '\nreturn nlStatusView;')();

const states = {
  ready: nlStatusView({ available: true, busy: false, loading: false, error: null }),
  running: nlStatusView({ available: true, busy: true, loading: false, error: null }),
  loading: nlStatusView({ available: false, busy: false, loading: true, error: null }),
  failed: nlStatusView({ available: true, busy: false, loading: false, error: 'model exploded' }),
  cold: nlStatusView({ available: false, busy: false, loading: false, error: null }),
};
for (const [k, v] of Object.entries(states)) {
  assert.ok(v.text && v.text.trim().length, k + ' must render text, never a blank status');
  assert.ok(/^nl-search-status(\s|$)/.test(v.cls), k + ' must carry the base class: ' + v.cls);
}
assert.ok(/busy/.test(states.running.cls), 'a running query must be visibly busy');
assert.ok(/loading/.test(states.loading.cls), 'a loading model must be visibly loading');
assert.ok(/ready/.test(states.ready.cls), 'an available model must read ready');

// The whole point: a failure must be distinguishable from a success.
assert.notStrictEqual(states.failed.cls, states.ready.cls, 'a failed query must not use the ready class');
assert.notStrictEqual(states.failed.text, states.ready.text, 'a failed query must not read ready');
assert.ok(/error/.test(states.failed.cls), 'a failed query must have its own error state');
assert.ok(/unavailable/i.test(states.failed.text) && /keyword/i.test(states.failed.text),
  'the error text must tell the user what still works: ' + states.failed.text);
assert.strictEqual(states.failed.title, 'model exploded', 'the raw reason must be kept in title');
// Precedence: busy wins over ready, loading, and error — a stale error from an
// earlier query must not hide a query that is actually running.
assert.strictEqual(nlStatusView({ busy: true, loading: true, available: true, error: 'old' }).cls,
  'nl-search-status busy');
assert.strictEqual(nlStatusView({ loading: true, available: true, error: 'old' }).cls,
  'nl-search-status loading');
// An error outranks "available" — a per-query failure must not read as ready.
assert.strictEqual(nlStatusView({ available: true, error: 'boom' }).cls, 'nl-search-status error');
// Defensive: no state at all still renders, and the fallthrough is not an error.
assert.ok(!/error/.test(nlStatusView({}).cls), 'a cold state must not read as an error');
assert.ok(!/error/.test(nlStatusView(null).cls), 'a missing state must not throw or read as an error');

// The .error colour has to be legible, and different from .ready.
const readyRule = rule('.nl-search-status.ready');
const errorRule = rule('.nl-search-status.error');
assert.ok(/color:/.test(errorRule), '.error must set its own colour');
assert.notStrictEqual(
  (readyRule.match(/var\(--[a-z-]+\)/) || [])[0],
  (errorRule.match(/var\(--[a-z-]+\)/) || [])[0],
  '.error must not reuse the .ready colour token');
assert.ok(/var\(--accent\)/.test(readyRule),
  '.ready keeps the accent (it already passes AA at 4.85:1 — the bug was the missing ' +
  'state machine, not the colour)');

// The error colour must clear WCAG AA on the popup footer's background.
const root = html.match(/:root\s*\{([\s\S]*?)\n\s*\}/);
const tokens = {};
for (const t of root[1].matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) tokens[t[1]] = t[2].trim();
const resolveToken = (v) => {
  let out = String(v);
  for (let i = 0; i < 6 && out.includes('var('); i++) {
    out = out.replace(/var\(\s*(--[a-z0-9-]+)\s*\)/g, (_, n) => tokens[n] || 'transparent');
  }
  return out;
};
const toRgb = (c) => {
  c = c.trim();
  const h = c.replace('#', '');
  const full = h.length === 3 ? h.split('').map(x => x + x).join('') : h;
  return [0, 2, 4].map(i => parseInt(full.slice(i, i + 2), 16));
};
const lum = (c) => {
  const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
};
const ratio = (a, b) => {
  const [x, y] = [lum(toRgb(a)), lum(toRgb(b))];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
};
const popupBg = resolveToken((html.match(/\.search-popup-inner\s*\{[^}]*background:\s*([^;]+);/) || [, ''])[1] || 'var(--bg-secondary)');
const errColor = resolveToken((errorRule.match(/color:\s*([^;]+);/) || [])[1]);
const readyColor = resolveToken((readyRule.match(/color:\s*([^;]+);/) || [])[1]);
assert.ok(ratio(errColor, popupBg) >= 4.5,
  `.nl-search-status.error ${errColor} on ${popupBg} is ${ratio(errColor, popupBg).toFixed(2)}:1, under the 4.5:1 AA floor`);
assert.ok(ratio(readyColor, popupBg) >= 4.5,
  `.nl-search-status.ready ${readyColor} on ${popupBg} is ${ratio(readyColor, popupBg).toFixed(2)}:1, under the 4.5:1 AA floor`);

// --- the call sites: before the await, and in a finally
const run = grabArrow('runSearch');
const awaitAt = run.indexOf('await pending');
const preSync = run.lastIndexOf('syncNlStatus()', awaitAt);
assert.ok(awaitAt > 0, 'runSearch must await the NL translation');
assert.ok(preSync > 0 && preSync < awaitAt,
  'syncNlStatus() must run BEFORE the await, or .busy/.loading are unreachable (#77)');
const resolveAt = run.indexOf('resolveNaturalLanguage(');
assert.ok(resolveAt < awaitAt, 'the pending translation must be started before awaiting it');
assert.ok(resolveAt < preSync,
  'the translation must be kicked off first so state.nlSearch.busy is already set when the status paints');

const finallyAt = run.indexOf('finally');
assert.ok(finallyAt > awaitAt, 'runSearch must have a finally that runs after the await');
const finallyBody = run.slice(finallyAt);
assert.ok(/syncNlStatus\(\)/.test(finallyBody),
  'the finally must call syncNlStatus() so the status always leaves the running state');
assert.ok(/syncSearchPopupCount\(\)/.test(finallyBody),
  'the result count must refresh on every path, including a failed query');

// A thrown translation must be recorded, not swallowed into silence.
// `async function resolveNaturalLanguage` — a declaration, but the regex above
// only looks for `function NAME(` on its own line.
const resolveNl = grabFn('resolveNaturalLanguage');
assert.ok(/catch \(e\) \{[\s\S]*?state\.nlSearch\.error = /.test(resolveNl),
  'resolveNaturalLanguage must record the failure on state.nlSearch.error, or the error ' +
  'state can never be reached (#77)');
assert.ok(/state\.nlSearch\.error = null;[\s\S]{0,120}?state\.nlSearch\.lastResolved/.test(resolveNl),
  'a successful translation must clear a previous error');

// ================================================ 2. every result reaches the chrome
const viewTabStates = new Function(sentinel('viewTabStates') + '\nreturn viewTabStates;')();
const FILTERS = ['events', 'objects', 'all'];
for (const view of FILTERS) {
  const s = viewTabStates(FILTERS, view);
  assert.deepStrictEqual(s, {
    events: view === 'events' ? 'true' : 'false',
    objects: view === 'objects' ? 'true' : 'false',
    all: view === 'all' ? 'true' : 'false',
  }, 'view ' + view + ': exactly its own tab may be pressed');
  assert.strictEqual(Object.values(s).filter(v => v === 'true').length, 1,
    'exactly one tab must be pressed for view ' + view);
}
assert.deepStrictEqual(viewTabStates(['a', 'b'], 'zzz'), { a: 'false', b: 'false' },
  'an unknown view must not leave a stale tab pressed');
assert.deepStrictEqual(viewTabStates(null, 'all'), {}, 'a missing tab list must not throw');

const sync = grabFn('syncViewChrome');
assert.ok(/viewTabStates\(/.test(sync), 'syncViewChrome must use the shared pressed-state helper');
assert.ok(/document\.body\.dataset\.view = v;/.test(sync),
  'syncViewChrome must set body[data-view] — it drives the chart tints');

// Every entry point that can move the view must repaint the view chrome.
for (const name of ['applyNaturalLanguageFilters', 'applySavedSearch', 'resetAllFilters']) {
  assert.ok(/syncViewChrome\(/.test(grabFn(name)),
    name + '() must call syncViewChrome() — an NL or restored view that skips it leaves the ' +
    'tab bar and body[data-view] on the old view (#77)');
}
const tabBarAt = html.indexOf("document.getElementById('filter-tabs').addEventListener('click'");
assert.notStrictEqual(tabBarAt, -1, 'the filter-tabs click handler moved; update this test');
const tabBar = html.slice(tabBarAt, tabBarAt + 1600);
assert.ok(/syncViewChrome\(\)/.test(tabBar),
  'the tab click handler must go through syncViewChrome() too, so there is one implementation');
// One implementation only: any other ad-hoc repaint of the tab bar is how the
// views desynced in the first place.
const repaints = html.match(/querySelectorAll\('#filter-tabs \.tab-btn'\)/g) || [];
assert.strictEqual(repaints.length, 1,
  'only syncViewChrome may query the tab buttons, found ' + repaints.length + ' sites');
// body[data-view] must be seeded, or the default view never gets its tints.
assert.ok(/\}\);(?:\s*\/\/[^\n]*\n)+\s*syncViewChrome\(\);/.test(tabBar),
  'body[data-view] must be seeded once at startup (#77)');

const nlApply = grabFn('applyNaturalLanguageFilters');
const changedAt = nlApply.indexOf('if (changed)');
assert.ok(changedAt > 0, 'applyNaturalLanguageFilters must have a changed block');
const changed = nlApply.slice(changedAt);
assert.ok(/updateSliderLabel\(\)/.test(changed),
  'the NL changed block must call updateSliderLabel() — an applied time range left ' +
  '#time-chip-label on "All day" and kept the whole time bar hidden (#77)');
assert.ok(/renderObjectFilters\(\)/.test(changed),
  'the NL changed block must call renderObjectFilters() — an applied objects filter lit up ' +
  'no chip and left #filters-active-count hidden (#77)');
assert.ok(/syncTimePills\(\)/.test(changed), 'syncTimePills() must stay in the changed block');
assert.ok(/applyFiltersAndSearch\(\)/.test(changed), 'the changed block must still re-render');

// updateSliderLabel is the only thing that un-hides the time bar, so assert the
// chain the browser audit measured: no range-collapsed -> toolbar bar visible.
const slider = grabFn('updateSliderLabel');
assert.ok(/wrapper\.classList\.toggle\('range-collapsed'/.test(slider),
  'updateSliderLabel must own range-collapsed (it is what hides the whole time bar)');
assert.ok(/chipLabel\.textContent = timeRangeIsDefault/.test(slider),
  'updateSliderLabel must own the #time-chip-label text');

console.log(
  'nl-search-states: ' + Object.keys(states).length + ' status states, error/ready contrast ' +
  ratio(errColor, popupBg).toFixed(2) + ':1 vs ' + ratio(readyColor, popupBg).toFixed(2) + ':1, ' +
  FILTERS.length + ' view tab states, 4 chrome chokepoints'
);
