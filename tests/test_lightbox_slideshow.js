// Node regression test for the CRITICAL lightbox slideshow freeze (#71).
//
// openLightbox() is re-entered on every auto-advance, so the pauseSlideshow()
// it used to call unconditionally fired on the very first tick: the slideshow
// advanced exactly one frame, cleared its own interval and sat there. This test
// runs the REAL openLightbox / navigateLightbox / resetSlideshowTimer /
// closeLightbox bodies from index.html against a minimal stub DOM and a fake
// clock + setInterval, so "does it keep advancing" is measurable, not asserted
// by reading the source.
//
// Run: node tests/test_lightbox_slideshow.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');

// Pull one top-level `function NAME(...) { ... }` out of the inline script.
function grabFn(name) {
  const at = html.indexOf('\n    function ' + name + '(');
  assert.notStrictEqual(at, -1, name + ' not found in index.html');
  let i = html.indexOf('{', at);
  let depth = 0;
  for (; i < html.length; i++) {
    if (html[i] === '{') depth++;
    else if (html[i] === '}' && --depth === 0) return html.slice(at, i + 1);
  }
  throw new Error('unbalanced braces in ' + name);
}

const FNS = [
  'updateZoomTransform', 'resetZoom', 'openLightbox', 'closeLightbox',
  'navigateLightbox', 'toggleSlideshow', 'startSlideshow', 'pauseSlideshow',
  'resetSlideshowTimer',
];
// The three module-level lets openLightbox/closeLightbox share.
const LETS = html.match(/let lightboxRelease = null;[\s\S]*?let lightboxOpenerFilename = null;/);

const source =
  'var lightboxRelease = null;\nvar lightboxOpener = null;\nvar lightboxOpenerFilename = null;\n' +
  FNS.map(grabFn).join('\n') +
  '\nreturn { openLightbox, closeLightbox, navigateLightbox, toggleSlideshow, startSlideshow, pauseSlideshow, resetSlideshowTimer };';

const STUBS = [
  'state', 'document', 'lucide', 'IMG_UNAVAILABLE', 'prefersReducedMotion', 'matchMedia',
  'parseFilename', 'cardAriaLabel', 'lightboxTitle', 'effectiveLabels', 'lightboxFooterMeta',
  'entryCaption', 'burstContaining', 'escapeHtml', 'shareLink', 'trapFocus',
  'setLightboxImageState', 'Image', 'Date', 'setInterval', 'clearInterval',
];

// ---------------------------------------------------------------- stub DOM
function el(id) {
  const classes = new Set();
  return {
    id,
    style: {},
    textContent: '',
    innerHTML: '',
    src: '',
    alt: '',
    dataset: {},
    setAttribute() {},
    getAttribute() { return null; },
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return el('inner'); },
    querySelectorAll() { return []; },
    focus() {},
    appendChild() {},
    contains() { return true; },
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
      toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
      get size() { return classes.size; },
    },
  };
}

const NODES = new Map();
const document = {
  activeElement: null,
  body: { style: {} },
  getElementById(id) {
    if (!NODES.has(id)) NODES.set(id, el(id));
    return NODES.get(id);
  },
  querySelector: () => null,
  contains: () => false,
};

// ------------------------------------------------------- fake clock/timers
let clock = 0;
let nextTimerId = 1;
const timers = new Map();
const setInterval = (cb, ms) => {
  const id = nextTimerId++;
  timers.set(id, { cb, ms, acc: 0 });
  return id;
};
const clearInterval = (id) => { timers.delete(id); };
// Advance the virtual clock in small steps, firing every due interval exactly
// as a real event loop would (including timers created from inside a callback).
function advance(ms, step) {
  step = step || 5;
  for (let t = 0; t < ms; t += step) {
    clock += step;
    for (const [id, t2] of [...timers]) {
      t2.acc += step;
      while (t2.acc >= t2.ms) {
        t2.acc -= t2.ms;
        t2.cb();
        // A callback that re-arms its own interval (resetSlideshowTimer does,
        // once per frame) retires this timer: stop rather than spin on a corpse.
        if (timers.get(id) !== t2) break;
      }
    }
  }
}

const state = {
  lightboxIndex: 0,
  // Long enough that 10 advances never wrap, so the index IS the advance count.
  filteredImages: Array.from({ length: 100 }, (_, i) => `10.0.0.21_01_2026061810${String(i).padStart(4, '0')}112_MOTDEC.jpg`),
  analysis: {},
  blacklist: [],
  bursts: [],
  isSlideshowPlaying: false,
  slideshowInterval: null,
  slideshowSpeed: 800,
  zoomScale: 1, zoomMin: 1, zoomMax: 5, translateX: 0, translateY: 0,
};

const api = new Function(...STUBS, source)(
  state, document, { createIcons() {} }, 'data:image/svg+xml,PLACEHOLDER',
  (m) => !!m, () => ({ matches: false }),
  (f) => ({ original: f, dateObj: null, formattedDate: 'Unknown Date', formattedTime: 'Unknown Time' }),
  () => 'alt', (m) => (m && m.original) || 'Image Details',
  () => new Set(), () => 'meta', () => '', () => null,
  (s) => String(s), () => {}, () => () => {},
  () => {}, function Image() { this.src = ''; },
  { now: () => clock }, setInterval, clearInterval
);

// ------------------------------------------------------------------- tests
api.openLightbox(0);
api.startSlideshow();
assert.strictEqual(state.isSlideshowPlaying, true, 'startSlideshow must set playing');
assert.strictEqual(timers.size, 1, 'exactly one slideshow interval must be armed');
assert.ok(state.slideshowInterval, 'slideshowInterval must be set');

const seen = new Set([state.lightboxIndex]);
for (let i = 0; i < 40; i++) { advance(200); seen.add(state.lightboxIndex); }

// THE regression: 8s at an 800ms dwell must move through ~10 frames and still
// be playing. Pre-fix this froze on the second frame.
assert.ok(seen.size >= 6, 'slideshow advanced only ' + (seen.size - 1) + ' frame(s) in 8s, expected >= 5');
assert.strictEqual(state.isSlideshowPlaying, true, 'slideshow must still be playing after 8s');
assert.ok(state.slideshowInterval, 'the interval must survive the advances');
assert.strictEqual(timers.size, 1, 're-arming must replace the timer, not stack them');
assert.strictEqual(state.lightboxIndex, 10, 'exactly 10 advances at an 800ms dwell over 8s');

// Each advance re-arms from scratch, so the progress bar restarts (no drift).
assert.strictEqual(document.getElementById('slideshow-progress').style.width, '0%');

// Manual navigation while playing keeps the timer alive (no double-speed-up).
const beforeManual = state.lightboxIndex;
api.navigateLightbox(1);
assert.strictEqual(state.lightboxIndex, (beforeManual + 1) % state.filteredImages.length);
assert.strictEqual(state.isSlideshowPlaying, true, 'manual nav must not stop the slideshow');
assert.strictEqual(timers.size, 1, 'manual nav must re-arm, not stack');
advance(1600);
assert.strictEqual(state.lightboxIndex, (beforeManual + 3) % state.filteredImages.length,
  'two more auto-advances after the manual one');

// Closing is the single place that stops it.
api.closeLightbox();
assert.strictEqual(state.isSlideshowPlaying, false, 'closeLightbox must pause the slideshow');
assert.strictEqual(state.slideshowInterval, null, 'closeLightbox must clear the interval');
assert.strictEqual(timers.size, 0, 'no timer may survive closeLightbox');
const frozen = state.lightboxIndex;
advance(4000);
assert.strictEqual(state.lightboxIndex, frozen, 'nothing may advance after closeLightbox');

// toggleSlideshow round-trips.
api.openLightbox(0);
api.toggleSlideshow();
assert.strictEqual(state.isSlideshowPlaying, true);
advance(2400);
assert.notStrictEqual(state.lightboxIndex, 0, 'toggleSlideshow must start the slideshow');
api.toggleSlideshow();
assert.strictEqual(state.isSlideshowPlaying, false);
assert.strictEqual(timers.size, 0);

console.log('lightbox slideshow: keeps advancing across frames (all assertions passed)');
