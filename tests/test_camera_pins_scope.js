// Per-camera pin visibility (#46). PR #39 made <camera_dir>/pins.json the
// canonical pins file but kept legacy repo-root pins protected, by union, in
// analyze_images.retention_pins() and in api_server.load_pins(include_legacy).
// The SPA bypassed both: it read the camera's raw pins.json, so on an upgraded
// install the frames retention still protects rendered as UNPINNED.
//
// The fix routes every pins read through GET /api/pins?camera=<id> (the one
// endpoint that unions legacy in) and paints the toggle from the server's
// response rather than from the click. Run: node tests/test_camera_pins_scope.js
const fs = require('fs');
const assert = require('assert');

const html = fs.readFileSync(__dirname + '/../index.html', 'utf8');
function block(name) {
  const m = html.match(new RegExp('pure:' + name + ' ===\\n([\\s\\S]*?)\\n\\s*// === /pure:' + name));
  assert(m, name + ' sentinel block not found in index.html');
  return m[1];
}
eval(block('resolveGalleryCameraId'));
eval(block('pinnedSetFor'));
eval(block('pinSetsEqual'));
eval(block('pinResponseState'));

// Full-line `//` comments stripped, so a prose mention of the old call in a
// comment cannot satisfy (or trip) a code-level assertion.
const code = html.split('\n')
  .filter(l => !l.trim().startsWith('//'))
  .join('\n');

// Brace-matched source of one top-level function, so a body containing nested
// blocks is not truncated by a naive indentation match.
function fn(name) {
  const start = code.indexOf('function ' + name);
  assert(start >= 0, name + ' not found in index.html');
  const open = code.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < code.length; i++) {
    if (code[i] === '{') depth++;
    else if (code[i] === '}' && --depth === 0) return code.slice(start, i + 1);
  }
  assert.fail('unbalanced braces in ' + name);
}

// ---------------------------------------------------------------------------
// resolveGalleryCameraId: the id the scoped reads and writes must agree on.
// ---------------------------------------------------------------------------
// A client-side switch pushStates /cameras/<id>/, so the route id is the only
// thing that survives a switch without a reload.
assert.strictEqual(resolveGalleryCameraId('Webcam22', 'Webcam21'), 'Webcam22',
  'the route camera must win over the web root that served the page');
assert.strictEqual(resolveGalleryCameraId(null, 'Webcam22'), 'Webcam22',
  'a camera-root page has no route id and falls back to the served camera');
assert.strictEqual(resolveGalleryCameraId(null, null), 'Webcam21',
  'no route and no served root falls back to the default camera');
assert.strictEqual(resolveGalleryCameraId('', ''), 'Webcam21',
  'empty ids must not be sent as ?camera=');
assert.strictEqual(resolveGalleryCameraId(undefined, undefined), 'Webcam21');

// ---------------------------------------------------------------------------
// pinnedSetFor: the legacy union must survive into the rendered set.
// ---------------------------------------------------------------------------
// These are the exact filenames a legacy install would have. api_server
// .load_pins(include_legacy=True) returns them for ?camera=<id>; the helper's
// only job is to not throw them away on the way to state.pins.
const LEGACY_ONLY = 'Webcam21_20250101_120000_000001.jpg';   // repo-root pins.json
const CAMERA_OWN = 'Webcam21_20250102_130000_000002.jpg';   // <camera_dir>/pins.json
const OTHER_CAM = 'Webcam22_20250103_140000_000003.jpg';    // camera B's pins

let s = pinnedSetFor('Webcam21', [LEGACY_ONLY, CAMERA_OWN]);
assert(s instanceof Set, 'must return a Set');
assert(s.has(LEGACY_ONLY),
  'a legacy repo-root pin the API reports must stay pinned in the view');
assert(s.has(CAMERA_OWN), 'a camera-own pin must stay pinned');
assert.strictEqual(s.size, 2);

// The API scopes the union per camera, so a cross-camera filename cannot come
// back in a camera's payload — scoping is the contract this helper relies on,
// which is why the fetch below must name the camera explicitly.
assert.strictEqual(pinnedSetFor('Webcam21', []).size, 0, 'an empty list is no pins');
assert.strictEqual(pinnedSetFor('Webcam21', []).has(LEGACY_ONLY), false);

// Non-list bodies must not become pins. A 404 body, an HTML error page, or
// {error: ...} would otherwise Object.entries into nonsense filenames.
for (const bad of [null, undefined, {}, 'nope', 42, true]) {
  const out = pinnedSetFor('Webcam21', bad);
  assert(out instanceof Set && out.size === 0,
    'a non-list body must read as nothing pinned, not junk pins');
}
// No camera id means the request was never scoped: refuse to paint pins.
assert.strictEqual(pinnedSetFor(null, [LEGACY_ONLY]).size, 0,
  'pins must not be applied without a camera id');
// Junk entries are dropped rather than becoming a permanent pin badge.
const dirty = pinnedSetFor('Webcam21', [CAMERA_OWN, '', null, 7, {}, []]);
assert.deepStrictEqual([...dirty], [CAMERA_OWN],
  'only non-empty string filenames may enter the pin set');

// ---------------------------------------------------------------------------
// pinSetsEqual: the 30s poll re-reads /api/pins (no ETag), so an unchanged set
// must not force a full grid re-render.
// ---------------------------------------------------------------------------
assert(pinSetsEqual(new Set(), new Set()), 'two empty sets are equal');
assert(pinSetsEqual(new Set([CAMERA_OWN]), new Set([CAMERA_OWN])));
assert(pinSetsEqual(new Set([LEGACY_ONLY, CAMERA_OWN]),
                    new Set([CAMERA_OWN, LEGACY_ONLY])),
  'equality is membership, not insertion order');
const same = new Set([CAMERA_OWN]);
assert(pinSetsEqual(same, same), 'identity short-circuits');
assert(!pinSetsEqual(new Set([CAMERA_OWN]), new Set([LEGACY_ONLY])),
  'same size, different member -> changed');
assert(!pinSetsEqual(new Set([LEGACY_ONLY]), new Set([LEGACY_ONLY, CAMERA_OWN])),
  'a gained pin -> changed');
assert(!pinSetsEqual(new Set([LEGACY_ONLY]), new Set()),
  'an emptied pin set -> changed (the unpin case)');
assert(!pinSetsEqual(null, new Set()), 'a missing set is not equal to a real one');
assert(!pinSetsEqual(new Set(), undefined));
assert(pinSetsEqual(null, null), 'both missing is unchanged');

// ---------------------------------------------------------------------------
// pinResponseState: the server, not the click, decides the badge.
// ---------------------------------------------------------------------------
// POST /api/pin answers {ok, pinned} where pinned is the real post-state. When a
// frame is only in the legacy repo-root file, set_pin(..., False) rewrites that
// shared file — so the two can genuinely disagree and the UI must show the
// server's answer.
assert.strictEqual(pinResponseState({ ok: true, pinned: true }, false), true,
  'a server-confirmed pin must paint as pinned even if the click meant to unpin');
assert.strictEqual(pinResponseState({ ok: true, pinned: false }, true), false,
  'an unpin the server did not apply must not paint as pinned');
assert.strictEqual(pinResponseState({ ok: true, pinned: true }, true), true);
assert.strictEqual(pinResponseState({ ok: true, pinned: false }, false), false);
// A malformed body falls back to the intent rather than asserting a state the
// server never confirmed.
for (const bad of [null, undefined, {}, { ok: true }, { pinned: 'yes' }, 'nope', 0]) {
  assert.strictEqual(pinResponseState(bad, true), true, 'fallback keeps the pin intent');
  assert.strictEqual(pinResponseState(bad, false), false, 'fallback keeps the unpin intent');
}

// ---------------------------------------------------------------------------
// Wiring: the reads and the write go to the API, scoped to the active camera.
// ---------------------------------------------------------------------------
// The bug itself: a relative pins.json read is the camera's own file with no
// legacy union, so it can never see a legacy pin.
assert(!/fetch\(\s*'pins\.json'/.test(code),
  'pins must not be read as a relative pins.json: that file omits the legacy '
  + 'repo-root pins retention still protects');
assert(!/cat\.pins/.test(code),
  '/api/catalogs returns the raw pins.json for `pins`; it must not be the source');

const fetchPins = fn('fetchPinsFor');
assert(/\/api\/pins\?camera=/.test(fetchPins),
  'pins must be fetched from /api/pins?camera=<id>, the legacy-inclusive read');
assert(/encodeURIComponent\(cameraId\)/.test(fetchPins),
  'the camera id must be encoded into the query string');
assert(/pinnedSetFor\(cameraId, payload\)/.test(fetchPins),
  'fetchPinsFor must normalise through pinnedSetFor');

// Both gallery paths must use it: the served camera (loadData) and a
// client-side switch (loadGalleryForCamera).
const loadData = fn('loadData');
assert(/fetchPinsFor\(galleryCameraId\(\)\)/.test(loadData),
  'the served-camera gallery must fetch its pins from the API');
assert(/pinSetsEqual\(state\.pins, pinsResp\)/.test(loadData),
  'the poll must compare the fresh pin set before forcing a re-render');
assert(!/state\.pins = new Set\(await pinsResp\.json\(\)\)/.test(loadData),
  'state.pins must not be filled from a raw json() body any more');

const loadGallery = fn('loadGalleryForCamera');
assert(/fetchPinsFor\(cameraId\)/.test(loadGallery),
  'switching cameras must re-fetch pins for the camera being switched to');
assert(/state\.pins = new Set\(\);/.test(loadGallery),
  'the outgoing camera pins must be dropped up front, so a failed or slow '
  + 'switch cannot leave the previous camera pins on screen');
// The switch must ask about the camera it is switching TO, and name it once.
// scope=true here would append a second ?camera= from the route, so the right
// camera would depend on the server taking the first of the two.
assert(/\/api\/catalogs\?camera=\$\{encodeURIComponent\(cameraId\)\}`, \{\}, false\)/.test(loadGallery),
  'the switch must name the target camera in the path and not double it via withCamera');

// The toggle must paint the response, not the intent.
const togglePin = fn('togglePin');
assert(/const pinned = pinResponseState\(resp, intent\)/.test(togglePin),
  'the badge must be painted from the server response');
assert(/apiPost\('\/api\/pin'/.test(togglePin), 'the pin write must be kept');
assert(!/classList\.toggle\('pinned', intent\)/.test(togglePin),
  'the badge must not be toggled from the click intent');
assert(!/const pinned = !state\.pins\.has\(filename\)/.test(togglePin),
  'the optimistic local derivation is what painted an unconfirmed state');

// Writes must be scoped to the camera on screen. currentCameraId() used to
// match the path against the two legacy roots and answered "Webcam21" for every
// /cameras/<id>/ route, so a pin from camera B's gallery landed on camera A.
assert(/galleryCameraId\(\)/.test(fn('currentCameraId')),
  'the scoped write camera must come from the route, not a path regex');
assert(!/\/\(Webcam21\|Webcam22\)\//.test(code),
  'the legacy-root path match must not decide which camera a write targets');
assert(/encodeURIComponent\(currentCameraId\(\)\)/.test(fn('withCamera')),
  'withCamera must send the resolved camera id');

// Switching cameras must not race two loads of the same gallery.
assert(!/pushRoute\(\{ view: ROUTE_GALLERY, cameraId: [a-z.]+ \}\);\s*\n\s*loadGalleryForCamera\(/m.test(code),
  'pushRoute already loads the gallery; the extra call started a racing load '
  + 'whose stale response could repaint the new camera with the old pins');
assert(/let galleryLoadSeq = 0;/.test(code), 'gallery load sequence guard missing');
assert((code.match(/if \(seq !== galleryLoadSeq\) return;/g) || []).length >= 3,
  'both load paths must drop a stale response before it touches state');

console.log('camera-pins-scope: all assertions passed');
