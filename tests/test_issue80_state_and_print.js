// Regression tests for issue #80 items 6, 7, 11, 12 and 13 — the corrupt
// images.json header stat, the missing in-flight state on the write controls,
// the camera menu that ignored Escape and unmanaged focus, the total absence of
// print handling, and long flag names hard-clipping out of the badge row.
//
// inFlightState is a pure sentinel helper and is exercised numerically;
// withInFlight is driven against a fake element (it only ever touches
// get/setAttribute). The rest are contract tests over index.html.
//
// Run: node tests/test_issue80_state_and_print.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const markup = html.replace(/<!--[\s\S]*?-->/g, '');
const grabSentinel = (name) => {
  const m = html.match(new RegExp(`pure:${name} ===\\n([\\s\\S]*?)\\n\\s*// === \\/pure:${name}`));
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
};
// Harvest a function body by brace counting (same trick as
// tests/test_lightbox_slideshow.js) for the non-pure DOM helpers. Handles both
// `function name(...)` and `const name = (...) => {`.
const grabFn = (name) => {
  const decl = html.indexOf('function ' + name);
  const arrow = html.search(new RegExp('(?:const|let|var)\\s+' + name + '\\s*=\\s*(?:async\\s*)?\\('));
  const at = decl !== -1 ? decl : arrow;
  assert(at !== -1, name + ' not found in index.html');
  let i = html.indexOf('{', at);
  let depth = 0;
  for (; i < html.length; i++) {
    if (html[i] === '{') depth++;
    else if (html[i] === '}' && --depth === 0) return html.slice(at, i + 1);
  }
  throw new Error('unbalanced braces in ' + name);
};

// ---------------------------------------------------------------------------
// Item 6 — a corrupt images.json left the header stat on "Loading snapshots…"
// ---------------------------------------------------------------------------
const badFormat = html.match(/if \(!imagesList \|\| !Array\.isArray\(imagesList\)\) \{([\s\S]*?)\n        \}/);
assert(badFormat, 'the invalid-format branch not found');
assert(/getElementById\('stats-label'\)/.test(badFormat[1]),
  'an images.json that is not a list is as fatal as a 500/abort, so this branch\n' +
  '  must set #stats-label too — otherwise the header kept reading "Loading\n' +
  '  snapshots…" while the grid already said "Something went wrong"');
assert(/label\.textContent\s*=\s*k\.title/.test(badFormat[1]),
  'the header stat must show the same title the grid shows');
// The two fatal branches must agree, which is the whole defect.
const catchBranch = html.match(/catch \(err\) \{([\s\S]*?)\n      \} finally \{/);
assert(catchBranch && /label\.textContent\s*=\s*k\.title/.test(catchBranch[1]),
  'the 500/abort branch is the reference behaviour and must keep setting the title');

// ---------------------------------------------------------------------------
// Item 7 — the write controls had no in-flight state
// ---------------------------------------------------------------------------
eval(grabSentinel('inFlightState')); // defines inFlightState
eval(grabFn('withInFlight'));          // defines withInFlight

assert.deepStrictEqual(inFlightState(true), { busy: true, disabled: true });
assert.deepStrictEqual(inFlightState(false), { busy: false, disabled: false });
assert.deepStrictEqual(inFlightState(0), { busy: false, disabled: false },
  'a falsy "busy" is not busy');
// Same contract as clipButtonState: `disabled` is a logical flag the caller
// applies as aria-disabled, never as the `disabled` property, which blurs a
// focused button and drops focus to BODY (issue #75).
assert(!/\.disabled\s*=/.test(grabFn('withInFlight')),
  'withInFlight must not set the disabled property — that blurs the focused button');
assert(/aria-busy/.test(grabFn('withInFlight')), 'withInFlight must set aria-busy');
assert(/finally/.test(grabFn('withInFlight')),
  'the in-flight state must be cleared in a finally so a rejected POST cannot\n' +
  '  strand the control disabled forever');

const fakeEl = () => {
  const attrs = {};
  return { getAttribute: (k) => (k in attrs ? attrs[k] : null), setAttribute: (k, v) => { attrs[k] = String(v); } };
};

(async () => {
  // In flight while the POST is pending, cleared after it resolves.
  const el = fakeEl();
  let release;
  const pending = new Promise((r) => { release = r; });
  const run = withInFlight(el, () => pending);
  await new Promise((r) => setImmediate(r));
  assert.strictEqual(el.getAttribute('aria-busy'), 'true', 'busy during the await');
  assert.strictEqual(el.getAttribute('aria-disabled'), 'true', 'logically disabled during the await');
  release();
  await run;
  assert.strictEqual(el.getAttribute('aria-busy'), 'false', 'cleared after the await');
  assert.strictEqual(el.getAttribute('aria-disabled'), 'false');

  // A rejection must still clear it.
  const el2 = fakeEl();
  await withInFlight(el2, () => Promise.reject(new Error('API 500'))).catch(() => {});
  assert.strictEqual(el2.getAttribute('aria-busy'), 'false', 'cleared after a failed POST');
  assert.strictEqual(el2.getAttribute('aria-disabled'), 'false');

  // A second activation while in flight is dropped, not queued.
  const el3 = fakeEl();
  let calls = 0;
  let release3;
  const pending3 = new Promise((r) => { release3 = r; });
  const first = withInFlight(el3, () => { calls++; return pending3; });
  await withInFlight(el3, () => { calls++; });
  assert.strictEqual(calls, 1, 'a re-entrant activation must not fire a second POST');
  release3();
  await first;

  // Every control that awaits apiPost before it changes anything must use it.
  const pin = html.match(/async function togglePin\(filename, card\) \{[\s\S]*?\n    \}/);
  assert(pin && /withInFlight\(pinBtn/.test(pin[0]),
    'togglePin must mark the pin button in flight around POST /api/pin');
  for (const handler of ['deepPassesToggle.onclick', 'backfillToggle.onclick',
    'burstSummariesToggle.onclick', 'detectorSelect.onchange', 'sweepIntervalInput.onchange',
    'parkedMaskToggle.onclick', 'porchMaskToggle.onclick']) {
    const at = html.indexOf(handler);
    assert(at !== -1, handler + ' not found');
    assert(/withInFlight\(/.test(html.slice(at, at + 200)),
      handler + ' must mark itself in flight around its apiPost');
  }
  // The existing clipButtonState pattern must not be duplicated or weakened.
  assert(/=== pure:clipButtonState ===/.test(html), 'clipButtonState is the reference pattern');

  // -------------------------------------------------------------------------
  // Item 11 — the camera menu ignored Escape and unmanaged focus
  // -------------------------------------------------------------------------
  const trigger = markup.match(/<button id="btn-switch-feed"[\s\S]*?>/);
  assert(trigger, '#btn-switch-feed not found');
  assert(/aria-haspopup="menu"/.test(trigger[0]),
    'a role=menu trigger must declare aria-haspopup="menu"');
  assert(/aria-expanded="false"/.test(trigger[0]),
    'a menu trigger must expose aria-expanded so the state is announced');
  assert(/aria-controls="camera-switch-menu"/.test(trigger[0]), 'aria-controls must point at the menu');

  const keydown = grabFn('onSwitchMenuKeydown');
  assert(/'Escape'/.test(keydown), 'Escape must close the menu (it did nothing before)');
  assert(/closeSwitchMenu\(true\)/.test(keydown), 'Escape must return focus to the trigger');
  assert(/event\.key === 'Tab'/.test(keydown) && /preventDefault\(\)/.test(keydown),
    'Tab must be trapped inside the open menu, not walk out into #filter-tabs');
  assert(/ArrowDown|ArrowUp/.test(keydown), 'arrow keys must move between options');

  const render = html.match(/const renderCameraMenu = \(list\) => \{([\s\S]*?)\n      \};/);
  assert(render, 'renderCameraMenu not found');
  assert(/switchOptions\(\)\[0\]\.focus\(\)/.test(render[1]),
    'opening the menu must move focus to its first option');
  assert(/tabIndex = -1/.test(render[1]),
    'menu items are reached with arrows, not Tab (ARIA menu pattern)');
  // Closing must happen on pointerdown, not click: the popover covered the
  // header, so a click-only closer could never fire for what it covered.
  assert(/addEventListener\('pointerdown'/.test(html) && /closeSwitchMenu\(false\)/.test(html),
    'an outside pointerdown must dismiss the menu');
  // Below 960px the menu joins the flow so it cannot cover the tab row.
  assert(/@media[^{]*max-width:\s*960px[^{]*\{[\s\S]*?cam-menu-open[\s\S]*?position:\s*static/.test(html),
    'below 960px the open menu must take layout space instead of covering the\n' +
    '  Timeline/Objects/Motion tabs (elementFromPoint over them returned\n' +
    '  .camera-switch-option at 390px, i.e. they were untappable)');
  // Anchored to the chip, not to a wrapped flex line: it used to render at
  // y=-28, above the top of the page.
  assert(/\.camera-switch-menu\s*\{[^}]*top:\s*100%/.test(html),
    '.camera-switch-menu must be anchored under the chip (top: 100%)');

  // -------------------------------------------------------------------------
  // Item 12 — there was no @media print handling at all
  // -------------------------------------------------------------------------
  const printBlocks = markup.match(/@media\s*print\s*\{[\s\S]*?\n\s{0,6}\}/g) || [];
  assert(printBlocks.length > 0, 'the page had zero @media print rules: under print\n' +
    '  emulation the dark body background and .image-grid\'s max-height both survived,\n' +
    '  so only the visible slice of the gallery printed');
  const print = printBlocks.join('\n');
  assert(/body\s*\{[^}]*background:\s*#fff/.test(print), 'print must use white paper');
  assert(/body\s*\{[^}]*color:\s*#000/.test(print), 'print must use black text');
  assert(/\.image-grid\s*\{[^}]*max-height:\s*none/.test(print),
    '.image-grid max-height must be dropped so the whole gallery prints');
  assert(/\.image-grid\s*\{[^}]*overflow:\s*visible/.test(print),
    '.image-grid overflow must be visible so the whole gallery prints');

  // -------------------------------------------------------------------------
  // Item 13 — long flag names overflowed the badge row and were hard-clipped
  // -------------------------------------------------------------------------
  const badgeRule = html.match(/\.detection-badges \.badge\s*\{([^}]*)\}/);
  assert(badgeRule, 'no .detection-badges .badge rule');
  for (const decl of [/max-width:\s*100%/, /overflow:\s*hidden/, /white-space:\s*nowrap/]) {
    assert(decl.test(badgeRule[1]),
      '.detection-badges .badge needs ' + decl + ' (got: ' + badgeRule[1].trim() + ')');
  }
  // .badge is a flex container, so the ellipsis has to live on the inner span —
  // a flex item's min-width:auto would otherwise refuse to shrink.
  const spanRule = html.match(/\.detection-badges \.badge > span\s*\{([^}]*)\}/);
  assert(spanRule, 'no .detection-badges .badge > span rule');
  assert(/min-width:\s*0/.test(spanRule[1]), 'the label span needs min-width: 0 to shrink');
  assert(/text-overflow:\s*ellipsis/.test(spanRule[1]),
    'the label span must ellipsis; .image-card clips at overflow:hidden, so before\n' +
    '  this a 67-char flag overflowed the badge row by 94px and the card by 84px');
  // The full name has to be recoverable from the tooltip.
  const badgeMarkup = markup.match(/<span class="badge badge-ai[^"]*"[^>]*>/);
  assert(badgeMarkup, 'the detection badge markup not found');
  assert(/title="\$\{escapeHtml\(label\)\}"/.test(badgeMarkup[0]),
    'the badge must carry the full label in title= (it had no tooltip at all)');
  // Same treatment for the HA scene line, which is where long flag names land.
  assert(/<span class="scene-line" title="\$\{escapeHtml\(scene\)\}">/.test(markup),
    '.scene-line truncates with an ellipsis too and must expose the full scene');

  console.log('issue80 state/print/badges: all assertions passed');
})().catch((e) => { console.error(e); process.exit(1); });
