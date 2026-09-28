// Contract: the pipeline-status panel must be fully reachable at every width.
//
// MEASURED BUG (headless Chromium, synthetic fixtures):
//   mobile  390x844    panel 131px  content 705px  last row UNREACHABLE
//   desktop 1440x1000  panel 700px  content 705px  last row UNREACHABLE
//
// Desktop cause: `.blacklist-dropdown` caps every dropdown at
// `min(70vh, 100dvh - 12rem)` = 700px on a 1000px window -- 5px less than the
// panel's own ~705px of content, so the tail was cut off mid-heading.
//
// Mobile cause: the sheet rule set `position: fixed` with `top: 12px;
// bottom: 12px`, which stretches a fixed box between its two insets ONLY when
// the containing block is the viewport. An ancestor between the dropdown and
// the viewport establishes a containing block for fixed-position descendants,
// so the insets resolved against that box and the sheet collapsed to the
// height of the sticky header. The panel showed 5 of ~29 lines; every camera,
// disk-usage and queue row was unreachable on a phone.
//
// Fix: state an explicit height, because an explicit height always wins over
// the inset stretch regardless of what the containing block turns out to be.
//
// These are static assertions -- they pin the recipe, not the layout. The
// layout proof is the browser measurement quoted above. The mutation block at
// the bottom re-runs every check against the pre-fix CSS to prove each one
// can actually fail.
const assert = require('assert');
const { src, css, mediaBodies, createLoader } = require('./helpers/load.cjs');

const PHONE = /max-width:\s*768px/;

function check(html, label) {
  const L = createLoader(html, label);
  const sheet = L.css();

  // 1. the status panel must be allowed more than the shared 70vh default
  const own = [...sheet.matchAll(/#system-dropdown\s*\{([^}]*)\}/g)].map((m) => m[1]);
  assert.ok(own.length > 0, `${label}: #system-dropdown needs its own rule`);

  const desktopMax = own
    .map((b) => (b.match(/max-height:\s*([^;]+);/) || [])[1])
    .filter(Boolean)
    .find((v) => !/100dvh\s*-\s*24px/.test(v));
  assert.ok(desktopMax, `${label}: #system-dropdown must set a desktop max-height`);
  assert.ok(
    /880px/.test(desktopMax),
    `${label}: status panel must outgrow min(70vh,...) -- that default clipped ` +
      `its last section. got: ${desktopMax}`
  );

  // 2. on a phone it must be a fixed sheet with an EXPLICIT height
  const phone = L.mediaBodies(PHONE).find((b) => b.includes('#system-dropdown'));
  assert.ok(phone, `${label}: the <=768px sheet must mention #system-dropdown`);
  assert.ok(
    /position:\s*fixed/.test(phone),
    `${label}: the phone sheet must stay position:fixed`
  );

  const h = (phone.match(/(?<!max-)height:\s*([^;]+);/) || [])[1];
  assert.ok(
    h && /100dvh/.test(h),
    `${label}: the phone sheet needs an explicit height from 100dvh. top+bottom ` +
      `alone collapses to the containing block (~131px) and strands most of the ` +
      `panel. got: ${h}`
  );
}

// ---- the shipped stylesheet passes ----------------------------------------
check(src, 'index.html');

// ---- every assertion above can actually fail ------------------------------
// Re-run against the pre-fix CSS, with each fix removed independently.
const preFixDesktop = src.replace(
  /#system-dropdown \{\s*max-height: min\(calc\(100dvh - 5rem\), 880px\);\s*\}/,
  '#system-dropdown { /* no desktop max-height */ }'
);
const preFixMobile = src.replace(
  /#system-dropdown \{\s*height: calc\(100dvh - 24px\);\s*\}/,
  '#system-dropdown { /* inset-only sheet */ }'
);

assert.throws(() => check(preFixDesktop, 'desktop-regression'), /outgrow|desktop max-height/,
  'removing the desktop max-height must fail the desktop check');
assert.throws(() => check(preFixMobile, 'mobile-regression'), /explicit height/,
  'removing the explicit phone height must fail the mobile check');
// Target the phone sheet specifically -- the document has other
// `position: fixed` rules (the .menu primitive) that must not be touched.
assert.throws(
  () => check(
    src.replace(
      /(\.blacklist-dropdown,\s*\n\s*#filters-dropdown \{)\s*\n(\s*)position: fixed;/,
      '$1\n$2position: absolute;'
    ),
    'position-regression'
  ),
  /position:fixed/,
  'downgrading the sheet to absolute must fail the mobile check'
);

console.log('status panel reachability: all assertions passed');
