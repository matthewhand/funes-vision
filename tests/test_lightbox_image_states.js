// Structural contract for the lightbox and visit-player fixes (#71-#75).
// These assertions are about index.html itself -- the CSS that keeps the five
// header controls on screen and the DOM wiring that makes the behaviours real.
// Every block here fails against the pre-fix index.html.
// Run: node tests/test_lightbox_image_states.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

function rules(selector) {
  const re = new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}', 'g');
  const found = [...html.matchAll(re)].map(m => m[1]);
  assert(found.length, selector + ' rule not found');
  return found;
}
function rule(selector) {
  return rules(selector)[0];
}

// ===================================================================== #73
// A long unparseable filename used to give .lightbox-title a min-content width
// wider than the viewport, so ALL FIVE controls landed off-screen and were
// unclickable at >=768px (the flex-wrap escape hatch was <=768px only).
const title = rule('.lightbox-title');
assert(/min-width:\s*0/.test(title),
  '.lightbox-title needs min-width:0 so the flex item can shrink');
assert(/overflow:\s*hidden/.test(title), '.lightbox-title needs overflow:hidden');
assert(/text-overflow:\s*ellipsis/.test(title), '.lightbox-title needs an ellipsis');
assert(/white-space:\s*nowrap/.test(title), '.lightbox-title must be a single line');
assert.ok(!/text-overflow:\s*clip/.test(title), '.lightbox-title must not keep clip');

// The header must wrap at EVERY width, not only inside a max-width media query.
const headerBlocks = rules('.lightbox-header');
assert(headerBlocks.some(b => /flex-wrap:\s*wrap/.test(b)),
  '.lightbox-header must be allowed to wrap at all widths');
const mobileWrap = html.match(/@media\s*\(max-width:\s*768px\)\s*\{[^}]*\.lightbox-header/);
assert(!mobileWrap,
  'header wrapping must not be locked inside the <=768px media query any more');
// The mobile block still centers the controls.
assert.ok(/@media[^{]*max-width:\s*768px[^{]*\{[\s\S]*?\.lightbox-controls\s*\{[^}]*justify-content:\s*center/.test(html),
  'the <=768px block must keep centering .lightbox-controls');

// ===================================================================== #72
// Loading state: from the moment the new src is written until it resolves.
assert.ok(/\.lightbox-image-wrapper\.lb-loading::after/.test(html),
  'a lb-loading state must be styled on the image wrapper');
assert.ok(/\.lightbox-image\[data-state="loading"\]/.test(html),
  'the image must be hidden while the next frame is in flight');
assert.ok(/prefers-reduced-motion: reduce[\s\S]{0,200}lb-loading::after \{ animation: none/.test(html),
  'the loading shimmer must respect prefers-reduced-motion');
assert.ok(/\.lightbox-note:empty \{ display: none/.test(html),
  'the footer note must collapse when there is nothing to say');

// The unavailable fallback reuses the app's shared glyph, exactly like the
// cards and the flipbook.
assert.ok(/const IMG_UNAVAILABLE = "data:image\/svg\+xml/.test(html),
  'IMG_UNAVAILABLE must still exist for the cards');
assert.ok(/img\.onerror = \(\) => \{\s*img\.onerror = null;\s*img\.onload = null;\s*setLightboxImageState\('unavailable'\);\s*img\.src = IMG_UNAVAILABLE;/.test(html),
  'the lightbox image needs an onerror -> IMG_UNAVAILABLE fallback');
assert.ok(/setLightboxImageState\('loading'\)[\s\S]{0,400}img\.src = filename;/.test(html),
  'the loading state must be raised BEFORE the new src is written');

// The note node is part of the footer template openLightbox writes, so the
// async error (which lands after that innerHTML) has something to fill in.
const openLb = html.slice(html.indexOf('function openLightbox'), html.indexOf('function closeLightbox'));
assert.ok(/id="lightbox-note"/.test(openLb),
  'the lightbox footer template must carry the #lightbox-note node');
assert.ok(/class="lightbox-note" id="lightbox-note"/.test(html),
  '#lightbox-note must exist in the markup (tests/test_dom_refs.js contract)');

// ===================================================================== #71
// openLightbox re-enters on every auto-advance, so an unconditional
// pauseSlideshow() there killed the slideshow after exactly one frame.
const openLbBody = openLb.slice(openLb.indexOf('function openLightbox'));
assert.ok(!/^\s*pauseSlideshow\(\);/m.test(openLbBody),
  'openLightbox must not pause the slideshow (navigateLightbox re-enters it)');
const closeLb = html.slice(html.indexOf('function closeLightbox'), html.indexOf('function navigateLightbox'));
assert.ok(/pauseSlideshow\(\);/.test(closeLb),
  'closeLightbox must remain the single place that stops the slideshow');
// ...and the follow-up timer restart in navigateLightbox is now live code.
const navLb = html.slice(html.indexOf('function navigateLightbox'), html.indexOf('function toggleSlideshow'));
assert.ok(/if \(state\.isSlideshowPlaying\) \{\s*resetSlideshowTimer\(\);/.test(navLb),
  'navigateLightbox must restart the slideshow timer when still playing');

// ===================================================================== #74
// 1. The crossfade is gated on decode(), not on "src was assigned".
const openEvp = html.slice(html.indexOf('function openEventPlayer'), html.indexOf('// Copy a same-origin deep link'));
const showAt = openEvp.indexOf('const show = () => {');
const hideAt = openEvp.indexOf("incoming.classList.remove('evp-show')", showAt);
const srcAt = openEvp.indexOf('incoming.src = `thumbs/', showAt);

assert.ok(showAt !== -1 && hideAt !== -1 && srcAt !== -1, 'show() body not found');
assert.ok(hideAt < srcAt,
  'the incoming layer must be hidden before its src is set');
assert.ok(srcAt < openEvp.indexOf('whenDecoded();', srcAt),
  'the incoming layer must only be settled after its src is set');
assert.ok(srcAt < openEvp.indexOf('outgoing.classList.remove', srcAt),
  'the outgoing layer must stay up until the incoming one is under way');
// evp-show may only be ADDED from inside reveal(), which only the decode path
// calls - never inline in show() the way the pre-fix code did.
const showBody = openEvp.slice(showAt, openEvp.indexOf('// Drag the slider', showAt));
assert.strictEqual((showBody.match(/classList\.add\('evp-show'\)/g) || []).length, 1,
  'evp-show must be added in exactly one place');
assert.ok(/const reveal = \(\) => \{[\s\S]{0,200}?incoming\.classList\.add\('evp-show'\)/.test(showBody),
  'evp-show must be added from the reveal() closure');
assert.ok(openEvp.indexOf('incoming.decode()', showAt) !== -1,
  'the reveal must be gated on incoming.decode()');
assert.ok(/incoming\.complete && incoming\.naturalWidth > 0\) \{ reveal\(\);/.test(openEvp),
  'the reveal needs a `complete` fast path');
assert.ok(/whenDecoded\(\);/.test(openEvp), 'show() must settle the incoming layer');
assert.ok(/--evp-fade/.test(openEvp) && /frameFadeMs\(durations\[idx\]\)/.test(openEvp),
  'the fade must be capped per frame from the real dwell');
assert.ok(/transition: opacity var\(--evp-fade, 180ms\) ease/.test(html),
  'the CSS transition must read the capped --evp-fade variable');
// A late decode() must not reveal a frame the player has already left.
assert.ok(/const token = \+\+showToken/.test(openEvp) && /if \(token !== showToken \|\| !overlay\.isConnected\) return;/.test(openEvp),
  'a stale decode() must not reveal a frame the player has left');
// A rejected decode must NOT paint an empty layer (it retries the full-res).
assert.ok(/p\.then\(reveal, \(\) => \{\}\)/.test(openEvp),
  'a failed decode must fall through to the onerror fallback, not paint nothing');

// 2. prefers-reduced-motion is honoured at RUNTIME, not sampled once.
assert.ok(/matchMedia\('\(prefers-reduced-motion: reduce\)'\)/.test(openEvp),
  'the player must keep a MediaQueryList, not a one-shot sample');
assert.ok(/motionQuery\.addEventListener\('change', onMotionChange\)/.test(openEvp),
  'a change listener is required so a mid-session flip pauses playback');
assert.ok(/if \(reduceMotion\) pause\(\);/.test(openEvp),
  'the change handler must pause when motion is reduced');
assert.ok(/motionQuery\.removeEventListener\('change', onMotionChange\)/.test(openEvp),
  'the listener must be removed on close');
assert.ok(!/const reduceMotion = prefersReducedMotion\(\s*\n?\s*typeof matchMedia/.test(openEvp),
  'the old one-shot sample must be gone');

// 3. Arrow keys must not double-step while the scrub slider has focus.
assert.ok(/scrubOwnsArrowKey\(e\.target, e\.key\)/.test(openEvp),
  'the key handler must defer to the native range input');

// ===================================================================== #75
// 1. aria-disabled + early-return guard, never the `disabled` property.
const dlBlock = openEvp.slice(openEvp.indexOf('const dlBtn'), openEvp.indexOf("overlay.querySelector('#evp-share')"));
assert.ok(!/dlBtn\.disabled = /.test(dlBlock),
  'setting .disabled on the focused GIF button drops focus to BODY');
assert.ok(/dlBtn\.setAttribute\('aria-disabled', String\(s\.disabled\)\)/.test(dlBlock),
  'the GIF button must be marked aria-disabled instead');
assert.ok(/dlBtn\.onclick = async \(\) => \{\s*if \(dlBtn\.getAttribute\('aria-disabled'\) === 'true'\) return;/.test(dlBlock),
  'the click handler needs an early-return guard');
assert.ok(/id="evp-download"[^>]*aria-disabled="false"/.test(openEvp),
  'the GIF button must start out aria-disabled="false"');

// 2. A live region for the label, the build progress and the outcome.
assert.ok(/id="evp-label" role="status"/.test(openEvp),
  '#evp-label must be a live region so per-frame changes are announced');
assert.ok(/id="evp-status" class="evp-sr" role="status"/.test(openEvp),
  'a live region is needed for the clip build/success states');
assert.ok(/\.evp-sr \{/.test(html) && /clip-path: inset\(50%\)/.test(html),
  '.evp-sr must visually hide the live region');
assert.ok(/statusEl\.textContent = s\.busy \? s\.text : dlMsg/.test(dlBlock),
  'the live region must carry the build progress and the outcome');
assert.ok(/showToast\('GIF downloaded — ' \+ name\)/.test(dlBlock),
  'a successful export must be announced, not silent');

// 3. The slider announces which frame; play/pause is named for its state.
assert.ok(/scrubEl\.setAttribute\('aria-valuetext',\s*frameAnnouncement/.test(openEvp),
  'show() must set aria-valuetext on the scrub slider');
assert.ok(/toggleEl\.setAttribute\('aria-label', s\.playing \? 'Pause playback' : 'Play playback'\)/.test(openEvp),
  'the play/pause name must follow its state');
assert.ok(!/aria-label="Play or pause"/.test(openEvp),
  'the static "Play or pause" name must be replaced');

// 4. A 200 that is not a GIF is an error, not a corrupt download.
assert.ok(/isGifPayload\(blob\.type, head\)/.test(dlBlock),
  'the downloaded blob must be validated as a GIF');
assert.ok(/throw new Error\('clip: not a GIF/.test(dlBlock),
  'a non-GIF body must throw into the existing catch so the user is told');

console.log('lightbox image states + player wiring: all assertions passed');
