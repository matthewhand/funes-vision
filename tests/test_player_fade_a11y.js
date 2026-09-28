// Node assert tests for the pure helpers behind the lightbox and visit-player
// fixes for issues #71-#75. Each of these FAILS against the pre-fix index.html
// (the helper does not exist, or the sentinel block is absent).
//   frameFadeMs        - #74 crossfade cap: a fixed 180ms fade outlasts an
//                        80ms burst dwell, smearing frame N-1 over frame N.
//   scrubOwnsArrowKey  - #74 arrow keys double-stepped while the scrub slider
//                        had focus, desyncing label from slider.
//   frameAnnouncement  - #75 the slider announced a bare integer, not which
//                        frame of the visit or when it was shot.
//   isGifPayload       - #75 /api/clip answers 200 text/html from a login page
//                        or the screenshot stub, saved as a corrupt .gif.
//   lightboxImageState - #72 a missing JPEG rendered as featureless black, and
//                        the previous frame stayed painted under the new
//                        title while the next one transferred.
// Run: node tests/test_player_fade_a11y.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
const grab = (name) => {
  const m = html.match(
    new RegExp('pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*\\/\\/ === \\/pure:' + name)
  );
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
};
eval(grab('frameFadeMs'));
eval(grab('scrubOwnsArrowKey'));
eval(grab('frameAnnouncement'));
eval(grab('isGifPayload'));
eval(grab('lightboxImageState'));

// ---------------------------------------------------------------- frameFadeMs
// 180ms is the ceiling, never the floor.
assert.strictEqual(frameFadeMs(4000), 180);
assert.strictEqual(frameFadeMs(360), 180);
// The regression: an 80ms burst dwell must NOT get a 180ms fade.
assert.strictEqual(frameFadeMs(80), 40);
assert.strictEqual(frameFadeMs(60), 30);
assert.strictEqual(frameFadeMs(120), 60);
// Always at most half the dwell, so the fade finishes inside the frame.
for (const d of [1, 7, 40, 80, 81, 179, 180, 181, 400, 2500]) {
  assert.ok(frameFadeMs(d) <= d / 2, 'fade ' + frameFadeMs(d) + ' must be <= ' + d / 2);
  assert.ok(frameFadeMs(d) <= 180, 'fade must never exceed the 180ms ceiling');
}
// Degenerate dwells fade instantly rather than freezing mid-transition.
assert.strictEqual(frameFadeMs(0), 0);
assert.strictEqual(frameFadeMs(-5), 0);
assert.strictEqual(frameFadeMs(NaN), 0);
assert.strictEqual(frameFadeMs(undefined), 0);
assert.strictEqual(frameFadeMs(null), 0);
// A custom ceiling is honoured and still capped by dwell/2.
assert.strictEqual(frameFadeMs(4000, 60), 60);
assert.strictEqual(frameFadeMs(80, 60), 40);

// ------------------------------------------------------------ scrubOwnsArrowKey
// The document-level stepper stands down when the key targets the range input.
const range = { closest: (sel) => (sel === 'input[type="range"]' ? {} : null) };
const plain = { closest: () => null };
assert.strictEqual(scrubOwnsArrowKey(range, 'ArrowRight'), true);
assert.strictEqual(scrubOwnsArrowKey(range, 'ArrowLeft'), true);
// Anything else still steps (arrows are the step keys; nothing else claims them).
assert.strictEqual(scrubOwnsArrowKey(range, 'ArrowUp'), false);
assert.strictEqual(scrubOwnsArrowKey(range, 'ArrowDown'), false);
assert.strictEqual(scrubOwnsArrowKey(range, ' '), false);
assert.strictEqual(scrubOwnsArrowKey(range, 'Escape'), false);
// Focus outside the slider (or on nothing) keeps the old single-step behaviour.
assert.strictEqual(scrubOwnsArrowKey(plain, 'ArrowRight'), false);
assert.strictEqual(scrubOwnsArrowKey(null, 'ArrowRight'), false);
assert.strictEqual(scrubOwnsArrowKey(undefined, 'ArrowLeft'), false);
assert.strictEqual(scrubOwnsArrowKey({}, 'ArrowRight'), false); // no .closest

// -------------------------------------------------------------- frameAnnouncement
const d = new Date(Date.UTC(2026, 5, 18, 0, 14, 18));
assert.strictEqual(frameAnnouncement(2, 8, d, 'UTC'), 'frame 3 of 8, 00:14:18');
assert.strictEqual(frameAnnouncement(0, 6, d, 'UTC'), 'frame 1 of 6, 00:14:18');
// No instant (unparseable name) still yields the frame identity.
assert.strictEqual(frameAnnouncement(2, 8, null, 'UTC'), 'frame 3 of 8');
assert.strictEqual(frameAnnouncement(2, 8, new Date(NaN), 'UTC'), 'frame 3 of 8');
// Index is clamped to the frame list; total of 0 does not produce "frame 1 of 0".
assert.strictEqual(frameAnnouncement(99, 8, null, 'UTC'), 'frame 8 of 8');
assert.strictEqual(frameAnnouncement(-3, 8, null, 'UTC'), 'frame 1 of 8');
assert.strictEqual(frameAnnouncement(0, 0, null, 'UTC'), 'frame 1 of 0');
// A zone the runtime cannot resolve degrades to the frame count, not a throw.
assert.strictEqual(frameAnnouncement(0, 3, d, 'Not/AZone'), 'frame 1 of 3');

// ------------------------------------------------------------------ isGifPayload
assert.strictEqual(isGifPayload('image/gif'), true);
assert.strictEqual(isGifPayload('image/gif; charset=binary'), true);
assert.strictEqual(isGifPayload('IMAGE/GIF'), true);
// The exact defect in #75: 200 + text/html saved as fast60.gif, silently.
assert.strictEqual(isGifPayload('text/html'), false);
assert.strictEqual(isGifPayload('application/json'), false);
assert.strictEqual(isGifPayload('text/html', [71, 73, 70, 56, 57, 97]), false);
assert.strictEqual(isGifPayload(''), false);
assert.strictEqual(isGifPayload(null, null), false);
assert.strictEqual(isGifPayload('', []), false);
// No declared type: fall back to the GIF87a / GIF89a magic bytes.
assert.strictEqual(isGifPayload('', [71, 73, 70, 56, 55, 97]), true); // GIF87a
assert.strictEqual(isGifPayload('', [71, 73, 70, 56, 57, 97]), true); // GIF89a
assert.strictEqual(isGifPayload('', [60, 33, 48, 48, 48, 48]), false);
// A declared non-image type is never rescued by the magic bytes: the server said
// what it is, and the caller's error message quotes it.
assert.strictEqual(isGifPayload('image/png', [71, 73, 70, 56, 57, 97]), false);

// ---------------------------------------------------------- lightboxImageState
// 'loading' blanks the previous frame so frame N-1's pixels are never read
// under frame N's title/footer.
assert.deepStrictEqual(lightboxImageState('loading'), {
  status: 'loading', loading: true, note: false, noteText: '',
});
// 'unavailable' says so, in the footer, instead of leaving black.
assert.deepStrictEqual(lightboxImageState('unavailable'), {
  status: 'unavailable', loading: false, note: true, noteText: 'Image unavailable',
});
// 'loaded' (and anything unknown) clears both.
for (const s of ['loaded', undefined, null, 'nonsense']) {
  assert.deepStrictEqual(lightboxImageState(s), {
    status: 'loaded', loading: false, note: false, noteText: '',
  });
}

console.log('player fade/a11y helpers: all assertions passed');
