// Node tests for overlay stacking, lightbox announcements and the lightbox
// backdrop click (issue #79). All four defects were confirmed in a real
// headless browser:
//
//  1. .toast was z-index 1000 against a lightbox (2000) and a zone editor
//     (2100), so a Share click in the lightbox raised a toast that
//     elementFromPoint put *behind* IMG#lightbox-image. The Download button gave
//     no feedback at all.
//  2. #lightbox had no live region, so nothing was ever announced while
//     img.alt / #lightbox-title / #lightbox-footer were rewritten on every
//     navigation; there was no "3 of 12"; and the dialog carried only a static
//     aria-label="Image viewer" with a plain <div> title.
//  3. Ctrl+K is a document-level keydown the lightbox handler never stops, so
//     the palette opened BEHIND the lightbox (z 1000 vs 2000) and silently
//     re-filtered the grid; toggling again cleared body.style.overflow and
//     released the lightbox's scroll lock.
//  4. The <img> is width/height 100% of a full-viewport wrapper, so
//     elementFromPoint returned it at every corner and the e.target-based
//     backdrop guard was unreachable dead code — only Escape and × closed it.
//
// Run: node tests/test_overlay_stacking.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

function grabFn(name) {
  const m = new RegExp('\\n([ \\t]*)function ' + name + '\\(').exec(html);
  assert(m, name + ' not found in index.html');
  const at = m.index;
  let i = html.indexOf('{', at);
  let depth = 0;
  for (; i < html.length; i++) {
    if (html[i] === '{') depth++;
    else if (html[i] === '}' && --depth === 0) return html.slice(at, i + 1);
  }
  throw new Error('unbalanced braces in ' + name);
}

function sentinel(name) {
  const m = html.match(new RegExp('// === pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*// === /pure:' + name));
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
}

function rule(selector) {
  const re = new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}', 'g');
  const found = [...html.matchAll(re)].map(m => m[1]);
  assert(found.length, selector + ' rule not found');
  return found[0];
}

function zIndexOf(selector) {
  const m = rule(selector).match(/z-index:\s*(\d+)/);
  assert(m, selector + ' has no numeric z-index');
  return Number(m[1]);
}

// ============================================ 1. the toast must clear the overlays
const toastZ = zIndexOf('.toast');
const lightboxZ = zIndexOf('.lightbox');
const zoneZ = zIndexOf('.zone-editor');
assert.ok(toastZ > lightboxZ,
  `.toast z-index ${toastZ} must sit above .lightbox ${lightboxZ}, or a Share/Download ` +
  'confirmation is painted behind the photo (#79)');
assert.ok(toastZ > zoneZ,
  `.toast z-index ${toastZ} must sit above .zone-editor ${zoneZ} (#79)`);
// The visit player builds its overlay with an inline z-index:2000.
const playerZ = Number((html.match(/overlay\.style\.cssText = '[^']*z-index:(\d+)/) || [])[1]);
assert(playerZ, 'the visit player overlay z-index moved; update this test');
assert.ok(toastZ > playerZ, `.toast ${toastZ} must sit above the visit player ${playerZ} (#79)`);
// Still below the new-activity pill, which is the one thing that must win.
const pillZ = Number((html.match(/pill\.style\.cssText = '[^']*z-index:(\d+)/) || [])[1]);
assert.ok(toastZ < pillZ, `.toast ${toastZ} must stay below the activity pill ${pillZ}`);
// The toast must still be a surface notification, not a CTA block
// (test_toast_style.js owns that; this is the stacking half of the same rule).
assert.ok(/position:\s*fixed/.test(rule('.toast')), '.toast must stay position:fixed');

// ================================================= 2. the lightbox can announce
const lbTag = html.match(/<div class="lightbox" id="lightbox"[^>]*>/);
assert(lbTag, '#lightbox opening tag not found');
assert.ok(/aria-labelledby="lightbox-title"/.test(lbTag[0]),
  '#lightbox must be named by its (per-frame) title, not a static aria-label (#79)');
assert.ok(!/aria-label=/.test(lbTag[0]),
  'a static aria-label would shadow the live title; drop it (#79)');
assert.ok(/id="lightbox-title"/.test(html), '#lightbox-title must exist for aria-labelledby');

const footerTag = html.match(/<div class="lightbox-footer" id="lightbox-footer"[^>]*>/);
assert(footerTag, '#lightbox-footer opening tag not found');
assert.ok(/aria-live="polite"/.test(footerTag[0]),
  '#lightbox-footer must be a polite live region, or frame changes are never announced (#79)');
assert.ok(/aria-atomic="true"/.test(footerTag[0]),
  '#lightbox-footer must be atomic so the whole new meta is read, not just the diff (#79)');

// A live region must be a permanent node that is *updated*, not created per
// frame; the footer is rewritten with innerHTML, which is fine, but there must
// be exactly one and it must live inside #lightbox.
const liveInside = [...html.matchAll(/aria-live="polite"/g)].length;
assert.ok(liveInside >= 1, 'expected at least one polite live region');
const lbAt = html.indexOf('id="lightbox"');
const zoneAt = html.indexOf('id="zone-editor"');
const lbMarkup = html.slice(lbAt, zoneAt);
assert.ok(/id="lightbox-footer"[^>]*aria-live="polite"/.test(lbMarkup),
  'the lightbox live region must live inside #lightbox (#79)');

// N of M
const framePosition = new Function(sentinel('framePosition') + '\nreturn framePosition;')();
assert.strictEqual(framePosition(0, 12), '1 of 12');
assert.strictEqual(framePosition(2, 12), '3 of 12');
assert.strictEqual(framePosition(11, 12), '12 of 12');
assert.strictEqual(framePosition(0, 1), '1 of 1');
// A stale index (auto-refresh re-render) must not read "11 of 4".
assert.strictEqual(framePosition(10, 4), '4 of 4');
assert.strictEqual(framePosition(-3, 4), '1 of 4');
assert.strictEqual(framePosition(NaN, 4), '1 of 4', 'a non-integer index must not print NaN');
assert.strictEqual(framePosition(0, 0), '0 of 0', 'an empty result set must read 0 of 0');
assert.strictEqual(framePosition(3, -1), '0 of 0');
assert.ok(/framePosition\(index, N\)/.test(grabFn('openLightbox')),
  'openLightbox must render the position indicator from the filtered result set (#79)');
assert.ok(/id="lightbox-position"/.test(html), 'the position indicator needs a stable id');

// ============================================== 3. Ctrl+K must defer to a modal
const modalIsOpen = grabFn('modalIsOpen');
for (const [id, probe] of [
  ['lightbox', /lightbox[\s\S]*?classList\.contains\('active'\)/],
  ['zone-editor', /zone-editor[\s\S]*?!zone\.hidden/],
  ['visit player', /getElementById\('evp-stage'\)/],
]) {
  assert.ok(probe.test(modalIsOpen), 'modalIsOpen must detect the ' + id + ' (#79)');
}
const kdAt = html.indexOf("document.addEventListener('keydown', (e) => {\n        if ((e.metaKey");
assert.notStrictEqual(kdAt, -1, 'the Ctrl+K keydown handler not found');
const kd = html.slice(kdAt, kdAt + 700);
const bail = kd.indexOf('if (modalIsOpen()) return;');
assert.ok(bail > 0, 'the Ctrl+K handler must bail out while a modal owns the keyboard (#79)');
assert.ok(bail < kd.indexOf('e.preventDefault();'),
  'the modal check must come before preventDefault, or the key is still consumed');
// Both branches of the toggle sit after the bail, so with a modal open the
// palette is neither opened nor closed: a close would run closeSearchPopup(),
// which clears body.style.overflow and silently releases the lightbox's own
// scroll lock (issue #79).
for (const call of ['closeSearchPopup();', 'openSearchPopup();']) {
  const at = kd.indexOf(call);
  assert.ok(at > bail, call + ' must be unreachable while a modal is open (#79), found at ' + at);
}
// Belt and braces: the chokepoint itself refuses, so the trigger and any
// programmatic open cannot stack a second overlay either.
const openLb = grabFn('openSearchPopup');
const openBail = openLb.indexOf('if (modalIsOpen()) return;');
assert.ok(openBail > 0, 'openSearchPopup must refuse to open while a modal is open (#79)');
assert.ok(openBail < openLb.indexOf('closeAllPopovers('),
  'the modal check must come first, before any other overlay work');

// ============================================ 4. the backdrop click is reachable
const outsidePaintedImage = new Function(sentinel('outsidePaintedImage') + '\nreturn outsidePaintedImage;')();

// A 16:9 photo in a 16:9 box paints edge to edge: there is no backdrop, and
// therefore nothing may close.
const wide = { left: 0, top: 0, width: 1280, height: 720 };
for (const [x, y] of [[640, 360], [0, 0], [1279, 719]]) {
  assert.strictEqual(outsidePaintedImage(x, y, wide, 1280, 720), false,
    'a matching aspect ratio paints the whole box; ' + x + ',' + y + ' is photo, not backdrop');
}
// A 4:3 box (1200x900) with a 16:9 photo (1280x720): contain fits it to
// 1200x675, leaving 112.5px of letterbox above and below. Those are the points
// the audit clicked, plus the exact boundary.
const tall = { left: 0, top: 0, width: 1200, height: 900 };
for (const [x, y] of [[5, 5], [600, 20], [600, 112], [5, 100], [600, 790], [5, 800], [1195, 795], [1199, 899]]) {
  assert.strictEqual(outsidePaintedImage(x, y, tall, 1280, 720), true,
    x + ',' + y + ' is in the letterbox and must count as a backdrop click (#79)');
}
for (const [x, y] of [[600, 450], [0, 450], [1199, 450], [5, 400], [600, 113], [600, 787]]) {
  assert.strictEqual(outsidePaintedImage(x, y, tall, 1280, 720), false,
    x + ',' + y + ' is on the photo and must NOT close the lightbox');
}
// Offsets (a wrapper that does not start at the viewport origin) must be
// honoured: the letterbox travels with the box.
assert.strictEqual(outsidePaintedImage(5, 5, { left: 0, top: 100, width: 1200, height: 900 }, 1280, 720), true,
  'with the box starting at y=100 the painted band starts at y=212, so (5,5) is backdrop');
assert.strictEqual(outsidePaintedImage(600, 400, { left: 0, top: 100, width: 1200, height: 900 }, 1280, 720), false,
  'and (600,400) is on the photo');
assert.strictEqual(outsidePaintedImage(5, 300, { left: 0, top: 100, width: 1200, height: 900 }, 1280, 1080), true,
  'a 4:3 photo (1280x1080) in this box letterboxes left/right, so x=5 is backdrop at any y');
assert.strictEqual(outsidePaintedImage(600, 300, { left: 0, top: 100, width: 1200, height: 900 }, 1280, 1080), false,
  'and its centre is photo');
// Nothing painted / no box yet: the whole overlay is backdrop, so a stray click
// cannot wedge the dialog open.
assert.strictEqual(outsidePaintedImage(10, 10, tall, 0, 0), true, 'no intrinsic size -> backdrop');
assert.strictEqual(outsidePaintedImage(10, 10, tall, 1280, 0), true, 'zero height -> backdrop');
assert.strictEqual(outsidePaintedImage(10, 10, { left: 0, top: 0, width: 0, height: 0 }, 1280, 720), true,
  'a collapsed box -> backdrop');
assert.strictEqual(outsidePaintedImage(10, 10, null, 1280, 720), true, 'a missing box must not throw');
// A portrait photo letterboxes left/right instead.
assert.strictEqual(outsidePaintedImage(5, 450, tall, 720, 1280), true, 'portrait photo: left band is backdrop');
assert.strictEqual(outsidePaintedImage(600, 450, tall, 720, 1280), false, 'portrait photo: centre is photo');

// The handler must hit-test, not compare e.target — e.target is the <img>.
const lbHandlerAt = html.indexOf("document.getElementById('lightbox').addEventListener('click'");
assert.notStrictEqual(lbHandlerAt, -1, 'the lightbox click handler not found');
const lbHandler = html.slice(lbHandlerAt, lbHandlerAt + 1200);
assert.ok(!/e\.target\.id === 'lightbox'/.test(lbHandler),
  "e.target can never be #lightbox while the <img> covers the overlay — that guard is " +
  'unreachable dead code (#79)');
assert.ok(/outsidePaintedImage\(e\.clientX, e\.clientY, img\.getBoundingClientRect\(\),\s*img\.naturalWidth, img\.naturalHeight\)/.test(lbHandler),
  'the handler must hit-test the painted photo with the viewport coordinates (#79)');
assert.ok(/state\.zoomScale !== 1\) return;/.test(lbHandler),
  'zoomed in, the photo covers the overlay: there is no backdrop and nothing should close');
assert.ok(/closeLightbox\(\)/.test(lbHandler), 'a real backdrop click must close');
// Drawn-over chrome is excluded so the caption, the arrows and the controls are
// not accidental close buttons.
assert.ok(/e\.target\.closest\('button, a, input, \.lightbox-header, \.lightbox-footer, \.slideshow-progress'\)/.test(lbHandler),
  'lightbox chrome over the letterbox (title, footer text, arrows, progress) must not close (#79)');
// Escape and the × must keep working.
assert.ok(/if \(e\.key === 'Escape'\) closeLightbox\(\)/.test(html), 'Escape must still close the lightbox');

console.log(
  `overlay-stacking: toast z ${toastZ} > lightbox ${lightboxZ} / zone ${zoneZ} / player ${playerZ}, ` +
  'labelledby + polite footer live region + "N of M", Ctrl+K defers to a modal, ' +
  'backdrop = the contain-fitted letterbox'
);
