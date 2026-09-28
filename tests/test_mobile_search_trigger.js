// Regression test for issue #88: at 390px the search trigger spilled past its
// own border — the placeholder wrapped to four lines inside a 58px box and
// painted to x=99 (trigger right edge x=84), and the ⌘K chip sat at x 107-139,
// 23px past the border, on top of BUTTON#btn-filters (elementFromPoint over the
// chip's right edge returned the Filters button, not the chip).
//
// This is the static backstop; the proof is the headless-browser measurement in
// tools/screenshots/a11y_audit.js (the trigger now measures 63.8x38 at 390x844
// with every child inside it, the label on one line with an ellipsis, and the
// ⌘K chip display:none). Run: node tests/test_mobile_search_trigger.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

// Every `@media (max-width: 768px)` block, concatenated: these declarations are
// what apply on a phone, wherever in the cascade they sit.
const phone = [...html.matchAll(/@media\s*\(max-width:\s*768px\)\s*\{/g)]
  .map((m) => {
    // Walk braces from the opening one so nested rules are included verbatim.
    let depth = 0;
    for (let i = m.index + m[0].length - 1; i < html.length; i++) {
      if (html[i] === '{') depth++;
      else if (html[i] === '}' && --depth === 0) return html.slice(m.index, i + 1);
    }
    return html.slice(m.index);
  })
  .join('\n');
assert(phone.length > 200, 'expected at least one @media (max-width: 768px) block');

// --- 1. The label truncates instead of wrapping, at any width -----------------
// A flex item's automatic minimum size is its content width, so without an
// explicit min-width:0 the label refuses to shrink below the full text and
// spills out of the trigger.
const labelRule = html.match(/\.search-trigger \.search-placeholder\s*\{([^}]*)\}/);
assert(labelRule, 'expected a .search-trigger .search-placeholder rule');
for (const [prop, why] of [
  ['min-width:\\s*0', 'a flex item must be allowed to shrink below its text width'],
  ['overflow:\\s*hidden', 'the text must be clipped to the trigger box'],
  ['text-overflow:\\s*ellipsis', 'the clipped text must read as truncated'],
  ['white-space:\\s*nowrap', 'the label must stay on one line'],
]) {
  assert(new RegExp(prop).test(labelRule[1]),
    `.search-trigger .search-placeholder must declare ${prop} -- ${why}`);
}

// --- 2. The phone trigger claims the row -------------------------------------
// flex: 1 1 120px let the trigger collapse to 64px wide with its content
// hanging outside; the basis is now the full row.
const phoneTrigger = phone.match(/\.search-trigger\s*\{([^}]*)\}/);
assert(phoneTrigger, 'expected a .search-trigger rule inside @media (max-width: 768px)');
assert(/flex:\s*1 1 100%/.test(phoneTrigger[1]),
  'the phone .search-trigger must use flex: 1 1 100% so it is not squeezed by its basis');
assert(!/flex:\s*1 1 120px/.test(phoneTrigger[1]),
  'the phone .search-trigger must not keep flex: 1 1 120px (issue #88)');
assert(/min-width:\s*0/.test(phoneTrigger[1]),
  'the phone .search-trigger must keep min-width: 0');

// --- 3. The ⌘K chip is a desktop affordance ----------------------------------
const kbdHide = phone.match(/\.search-trigger \.search-kbd\s*\{([^}]*)\}/);
assert(kbdHide, 'expected a .search-trigger .search-kbd rule inside the phone media block');
assert(/display:\s*none/.test(kbdHide[1]),
  '.search-kbd must be display:none on narrow viewports (it overlapped #btn-filters, issue #88)');

// --- 4. The visible label comes from the same string as the popup input -------
// The trigger used to keep the long desktop wording on a phone regardless of
// syncSearchPlaceholder(), so the comment claiming the phone clips the label was
// false. One string now feeds both.
const textFn = html.match(/const searchPlaceholderText = \(\) =>([\s\S]*?);\n/);
assert(textFn, 'expected a searchPlaceholderText() helper');
assert(/matchMedia\('\(max-width:\s*768px\)'\)/.test(textFn[1]),
  'searchPlaceholderText() must switch on the phone breakpoint');
assert(/'Search…'/.test(textFn[1]) && /'Search time, labels, or caption…'/.test(textFn[1]),
  'searchPlaceholderText() must carry both the phone and desktop wording');

const sync = html.match(/const syncSearchPlaceholder = \(\) => \{([\s\S]*?)\n {6}\};/);
assert(sync, 'expected a syncSearchPlaceholder() body');
assert(/si\.placeholder = text/.test(sync[1]),
  'the popup input placeholder must read from searchPlaceholderText()');
assert(/search-trigger/.test(sync[1]) && /ph\.textContent = text/.test(sync[1]),
  'the visible trigger label must be driven by the same string (issue #88)');
assert(/dataset\.query/.test(sync[1]),
  'syncSearchPlaceholder() must not stomp the label while it echoes an open query');

// closeSearchPopup() resets the label to the hint -- from that same string, so
// closing the popup on a phone cannot restore the long wording.
const closeFn = html.match(/function closeSearchPopup\(\) \{([\s\S]*?)\n {6}\}/);
assert(closeFn, 'expected a closeSearchPopup() body');
assert(/ph\.textContent = v \|\| searchPlaceholderText\(\)/.test(closeFn[1]),
  'closeSearchPopup() must reset the trigger label via searchPlaceholderText()');

console.log('mobile-search-trigger: all assertions passed');
