// Unit tests for the pieces tools/screenshots/a11y_audit.js added to stop the
// headless-browser contrast gate passing vacuously (issue #116).
//
// #116 in one line: PR #112 put a radial-gradient on <body>, the collector
// treated ANY background-image as "cannot measure", so every text node in
// every state came back as a skip and text-contrast reported PASS over 0 of 84
// measured nodes. The fix measures gradients (worst case across their colour
// stops) and adds a coverage gate that fails when a state measures nothing.
//
// Everything asserted here is pure: no Chromium, no proxy, no fixtures. The
// browser-backed proof of the same behaviour lives in the script's
// --selfcheck, which CI runs in the browser-a11y-gate job.
//
// Run: node tests/test_a11y_coverage_gate.js
const assert = require('assert');
const path = require('path');

const audit = require(path.join(__dirname, '..', 'tools', 'screenshots', 'a11y_audit.js'));
const {
  THRESHOLDS, CHECKS, SKIP_EXCEPTIONS,
  checkTextContrastCoverage, checkTextContrast, checkThemeApplied,
  inPageGradientStops, inPageBgImageLayers, inPageBgLayer, inPagePaintLayers,
  inPageCompositeLayer, inPageBgVariants,
  worstCasePair, summarise, summariseCoverage, withRatios, resolveThemes,
  contrastRatio, inPageBundleSource,
  inPageInsideDisabledControl, inPageMediaShown, inPageOverMedia,
} = audit;

const OVER = (r, g, b, a) => ({ r, g, b, a: a === undefined ? 1 : a });
const CANVAS = () => [OVER(255, 255, 255, 1)];

// ---------------------------------------------------------------------------
// 1. Gradient stop extraction: every colour in the value, or a refusal.
// ---------------------------------------------------------------------------
// The exact value Chrome computes for index.html's dark body background.
assert.deepStrictEqual(
  inPageGradientStops('radial-gradient(ellipse 1100px 650px at 70% -320px, rgba(39, 105, 178, 0.19), rgba(0, 0, 0, 0) 72%)'),
  { stops: [OVER(39, 105, 178, 0.19), OVER(0, 0, 0, 0)] },
  'the app body gradient must yield its rgba stop and its transparent tail');

// `transparent` is a stop too: it composites to the layer beneath it, which is
// how the plain background colour stays in the worst-case set.
assert.deepStrictEqual(
  inPageGradientStops('linear-gradient(180deg, #ffffff, transparent)'),
  { stops: [OVER(255, 255, 255), OVER(255, 255, 255, 0)] },
  'the transparent tail must be a stop, not a dropped token');

// Hex, 3-digit hex and hsl all have to be read, because a stylesheet is free
// to spell its stops any of those ways.
assert.deepStrictEqual(
  inPageGradientStops('linear-gradient(#fff, #000000, hsl(120, 50%, 50%))'),
  { stops: [OVER(255, 255, 255), OVER(0, 0, 0), OVER(63.75, 191.25, 63.75)] },
  'hex, 6-digit hex and hsl stops must all be extracted');

// A colour space this gate cannot read must be reported, not silently dropped:
// measuring only the stops we recognise could pass text painted in the colour
// we failed to parse.
assert.deepStrictEqual(
  inPageGradientStops('linear-gradient(in oklab, oklab(0.5 0.1 0.1), #000)'),
  { unparsed: 'oklab(...)' },
  'an unreadable colour space must be reported');
assert.deepStrictEqual(
  inPageGradientStops('linear-gradient(color-mix(in srgb, red, blue), #000)'),
  { unparsed: 'color-mix(...)' },
  'color-mix() is a colour this gate cannot read');
assert.strictEqual(inPageGradientStops('linear-gradient(90deg, #fff, #000)').unparsed, undefined,
  'a plain gradient must parse with no unparsed residue');

// The geometry in a gradient is not a colour: no phantom stops.
assert.strictEqual(
  inPageGradientStops('radial-gradient(circle 400px at 70% -320px, #112233 0%, #445566 100%)').stops.length, 2,
  'only the colour stops come back, never the geometry');

// ---------------------------------------------------------------------------
// 2. Layer list: split on top-level commas only, then classify.
// ---------------------------------------------------------------------------
assert.deepStrictEqual(inPageBgImageLayers('none'), [], 'none has no layers');
assert.deepStrictEqual(
  inPageBgImageLayers('url(a.png), linear-gradient(#fff, #000)'),
  ['linear-gradient(#fff, #000)', 'url(a.png)'],
  'the layer list must be reversed to bottom-first paint order');
assert.deepStrictEqual(
  inPageBgImageLayers('radial-gradient(ellipse 1100px 650px at 70% -320px, rgba(1, 2, 3, .2), transparent 72%), url(b.jpg)'),
  ['url(b.jpg)', 'radial-gradient(ellipse 1100px 650px at 70% -320px, rgba(1, 2, 3, .2), transparent 72%)'],
  'commas inside a gradient must not split the layer list');

// ---------------------------------------------------------------------------
// 2b. `none` is a LAYER, not a value: Chrome serialises
// getComputedStyle(body).backgroundImage as
//   radial-gradient(1050px 580px at 70% -350px, rgba(126, 180, 244, 0.19), rgba(0, 0, 0, 0) 72%), none
// i.e. a list whose last entry is the `none` keyword. Such a layer paints
// nothing, so dropping it is what stops the whole document from being a skip
// (the first attempt at the fix reported 0 of 674 text nodes measured).
// ---------------------------------------------------------------------------
const BODY_BG = 'radial-gradient(1050px 580px at 70% -350px, rgba(126, 180, 244, 0.19), rgba(0, 0, 0, 0) 72%), none';
const BODY_GRAD = 'radial-gradient(1050px 580px at 70% -350px, rgba(126, 180, 244, 0.19), rgba(0, 0, 0, 0) 72%)';

assert.deepStrictEqual(
  inPageBgImageLayers(BODY_BG), [BODY_GRAD],
  'the `none` entry of the computed body background must be dropped from the list');
assert.deepStrictEqual(
  inPageBgImageLayers(BODY_GRAD + ', none'), [BODY_GRAD],
  'a trailing `none` layer must not survive the split');
assert.deepStrictEqual(
  inPageBgImageLayers('none, ' + BODY_GRAD), [BODY_GRAD],
  'a leading `none` layer must not survive the split');
assert.deepStrictEqual(
  inPageBgImageLayers(BODY_GRAD + ',   none  , linear-gradient(#000, #fff)'),
  ['linear-gradient(#000, #fff)', BODY_GRAD],
  'a `none` between two real layers must be dropped, order kept');
assert.deepStrictEqual(inPageBgImageLayers('none, none'), [],
  'a list of nothing but `none` still paints nothing');
assert.deepStrictEqual(inPageBgImageLayers(''), [], 'the empty background paints nothing');
assert.deepStrictEqual(inPageBgImageLayers('NONE'), ['NONE'],
  'anything that is not exactly the keyword `none` stays unclassified');

// End to end: the body's own value must measure, for every element whose
// ancestor chain includes <body>.
{
  const paint = [];
  assert.strictEqual(inPagePaintLayers(BODY_BG, 'rgb(7, 17, 30)', 1, paint), null,
    'the computed body background must NOT be a skip');
  assert.strictEqual(paint.length, 2, 'the flat colour still paints beneath the gradient');
  assert.strictEqual(paint[0].kind, 'color');
  assert.strictEqual(paint[1].kind, 'gradient');

  // The 18%-blue stop is the worst case, exactly as in the real page.
  let cands = CANVAS();
  for (const l of paint) cands = inPageCompositeLayer(cands, l);
  const rounded = cands.map((c) => [Math.round(c.r), Math.round(c.g), Math.round(c.b)]);
  assert.strictEqual(cands.length, 2, 'the gradient stop and the transparent tail are two candidates');
  assert.ok(rounded.some((c) => c[0] === 30 && c[1] === 48 && c[2] === 71),
    'the 19% blue stop must be composited over --bg-primary (' + JSON.stringify(rounded) + ')');
  assert.ok(rounded.some((c) => c[0] === 7 && c[1] === 17 && c[2] === 30),
    'the transparent tail must hand the underlying colour back');
}

// A multi-gradient list (several layers, `none` among them) measures too.
{
  const paint = [];
  const multi = 'linear-gradient(rgba(255, 255, 255, .5), rgba(0, 0, 0, 0)), '
    + BODY_GRAD + ', none';
  assert.strictEqual(inPagePaintLayers(multi, 'rgb(7, 17, 30)', 1, paint), null,
    'a multi-gradient layer list must not skip');
  assert.strictEqual(paint.length, 3, 'the colour plus both gradients paint');
  assert.strictEqual(paint.filter((l) => l.kind === 'gradient').length, 2,
    'every gradient layer must contribute its stops');
}

// A `none` layer must not launder a layer that genuinely cannot be measured.
assert.strictEqual(
  inPagePaintLayers("url('thumbs/x.jpg'), none", 'rgb(7, 17, 30)', 1, []),
  'background-url-image',
  'a url() photo beside a `none` layer is still a skip');
assert.strictEqual(
  inPagePaintLayers('element(#paint), none', 'rgb(7, 17, 30)', 1, []),
  'unparsed-background',
  'an unknown image type beside a `none` layer is still a skip');
assert.strictEqual(
  inPagePaintLayers('none, element(#paint)', 'rgb(7, 17, 30)', 1, []),
  'unparsed-background',
  'the unknown layer must be classified wherever it sits in the list');
assert.strictEqual(
  inPagePaintLayers(BODY_GRAD + ', linear-gradient(#fff, color-mix(in srgb, red, blue))', 'rgb(7, 17, 30)', 1, []),
  'unparsed-gradient',
  'a colour this gate cannot read must still skip');

assert.strictEqual(inPageBgLayer("url('thumbs/x.jpg')").url, true,
  'a url() layer is a photo and stays a skip');
assert.deepStrictEqual(inPageBgLayer('linear-gradient(#fff, #000)').gradient,
  { stops: [OVER(255, 255, 255), OVER(0, 0, 0)] },
  'a gradient layer is measurable');
assert.strictEqual(inPageBgLayer('element(#paint)').unparsed, true,
  'an unknown image type must stay a skip, not be treated as a gradient');

// ---------------------------------------------------------------------------
// 3. Worst-case compositing: the plain colour AND every gradient stop.
// ---------------------------------------------------------------------------
// Body gradient over an opaque flat colour, exactly like the app's
// `background: radial-gradient(...), var(--bg-primary)`.
{
  const paint = [];
  const skip = inPagePaintLayers(
    'radial-gradient(ellipse 1100px 650px at 70% -320px, rgba(39, 105, 178, 0.19), transparent 72%)',
    'rgb(7, 17, 30)', 1, paint);
  assert.strictEqual(skip, null, 'a gradient-only background must NOT skip');
  assert.strictEqual(paint.length, 2, 'the colour paints beneath its image layer');
  assert.strictEqual(paint[0].kind, 'color');
  assert.strictEqual(paint[1].kind, 'gradient');

  let cands = CANVAS();
  for (const l of paint) cands = inPageCompositeLayer(cands, l);
  const rounded = cands.map((c) => [Math.round(c.r), Math.round(c.g), Math.round(c.b)]);
  assert.strictEqual(cands.length, 2, 'the gradient stop and the transparent tail are two candidates');
  assert.ok(rounded.some((c) => c[0] === 13 && c[1] === 34 && c[2] === 58),
    'the 19% blue stop must be composited over --bg-primary (' + JSON.stringify(rounded) + ')');
  assert.ok(rounded.some((c) => c[0] === 7 && c[1] === 17 && c[2] === 30),
    'the transparent tail must hand the underlying colour back');

  // The WORST case is what the gate acts on: 3.88:1 here vs 4.55:1 on the flat
  // colour, i.e. the gradient made this text measurably worse.
  const variants = inPageBgVariants(OVER(148, 163, 184), 1, cands);
  const ratios = variants.map((v) => contrastRatio(v.fgRgb, v.bgRgb));
  assert.strictEqual(Math.min(...ratios), ratios[0], 'the lighter stop must be the worst case');
  assert.ok(ratios[0] < ratios[1], 'the two candidates must give different ratios');
  assert.strictEqual(worstCasePair({ bgVariants: variants }).ratio, Math.min(...ratios),
    'worstCasePair() must pick the minimum ratio across the gradient');
}

// An opaque colour layer collapses the candidate set, which is what stops a
// deep ancestor chain from multiplying out.
{
  let cands = CANVAS();
  cands = inPageCompositeLayer(cands, { kind: 'gradient', stops: [OVER(255, 0, 0, 0.5), OVER(0, 0, 255, 0.5)], opacity: 1 });
  assert.strictEqual(cands.length, 2);
  cands = inPageCompositeLayer(cands, { kind: 'color', color: OVER(10, 20, 30), opacity: 1 });
  assert.strictEqual(cands.length, 1, 'an opaque layer must collapse the set to its own colour');
  assert.deepStrictEqual(cands.map((c) => [Math.round(c.r), Math.round(c.g), Math.round(c.b)]), [[10, 20, 30]]);
}

// An ancestor opacity scales the text and its background together.
{
  const variants = inPageBgVariants(OVER(0, 0, 0), 0.5, [OVER(255, 255, 255)]);
  assert.deepStrictEqual(variants[0].fgRgb, [128, 128, 128], 'opacity folds the fg onto its background');
}

// A url() photo is still a skip -- that is the only way to lose coverage now.
assert.strictEqual(
  inPagePaintLayers("url('thumbs/x.jpg')", 'rgb(7, 17, 30)', 1, []),
  'background-url-image',
  'a raster photo must still skip, with a reason the histogram can name');
// ...and so is a background colour this gate cannot parse.
assert.strictEqual(
  inPagePaintLayers('linear-gradient(#fff, color-mix(in srgb, red, blue))', 'rgb(7, 17, 30)', 1, []),
  'unparsed-gradient',
  'an unreadable gradient must skip rather than be guessed at');
assert.strictEqual(
  inPagePaintLayers('none', 'rebeccapurple', 1, []),
  'unparsed-background',
  'an unparseable background colour must stay a skip');

// ---------------------------------------------------------------------------
// 4. The coverage gate decision.
// ---------------------------------------------------------------------------
const THRESH = THRESHOLDS.minTextCoverage;
assert.strictEqual(THRESH, 0.8, 'the named coverage floor is 0.8');
assert.ok(THRESH > 0 && THRESH < 1, 'the floor must be a real fraction, not 0 or 1');

const rec = (o) => Object.assign({
  state: 'light/timeline', selector: 'p#x', text: 'label', fontSize: 12, large: false,
}, o);

// 0 measured: the vacuous pass, which must fail loudly and say why.
{
  const zero = Array.from({ length: 84 }, () => rec({ skip: 'background-url-image' }));
  const f = checkTextContrastCoverage(zero);
  assert.strictEqual(f.length, 1, 'a state that measures nothing must fail');
  assert.strictEqual(f[0].check, 'text-contrast-coverage');
  assert.ok(/measured 0 of 84/.test(f[0].detail), 'the finding must carry the numbers');
  assert.ok(/background-url-image:84/.test(f[0].detail),
    'the finding must carry the skip-reason histogram (that is how you debug it)');
  assert.ok(/80%/.test(f[0].fix), 'the fix must name the floor');
}

// Below the floor but not zero.
{
  const low = [
    ...Array.from({ length: 5 }, (_, i) => rec({ skip: 'unparsed-background', selector: 'p.u' + i })),
    ...Array.from({ length: 15 }, () => rec({ ratio: 12 })),
  ];
  const f = checkTextContrastCoverage(low);
  assert.strictEqual(f.length, 1, '75% coverage must fail the 80% floor');
  assert.ok(/75% coverage/.test(f[0].detail), 'the finding must quote the measured coverage');
  assert.ok(/unparsed-background:5/.test(f[0].detail), 'the histogram must name the reason');
  // The border is exactly the threshold: 16 of 20 is 80%, which passes.
  const atFloor = [
    ...Array.from({ length: 4 }, (_, i) => rec({ skip: 'unparsed-background', selector: 'p.u' + i })),
    ...Array.from({ length: 16 }, () => rec({ ratio: 12 })),
  ];
  assert.strictEqual(checkTextContrastCoverage(atFloor).length, 0,
    '80% coverage is exactly the floor and must pass');
  assert.strictEqual(checkTextContrastCoverage(atFloor.concat([rec({ skip: 'unparsed-background', selector: 'p.u9' })])).length, 1,
    'one more skip drops below the floor and must fail');
}

// A document, reviewed exception list waives the url()-photo nodes.
{
  const photo = [rec({ skip: 'background-url-image', selector: 'div#lightbox-footer' })];
  const waiver = [{ selector: '#lightbox-footer', why: 'caption painted over the full-size url() photo' }];
  assert.strictEqual(checkTextContrastCoverage(photo).length, 1, 'without a waiver the photo text fails');
  assert.strictEqual(checkTextContrastCoverage(photo, waiver).length, 0,
    'a documented exception list must waive the node');
  assert.strictEqual(checkTextContrastCoverage(photo, []).length, 1, 'an empty list waives nothing');
  // Only url()-photo skips are waivable: a missing colour is a bug to fix.
  const unparsed = [rec({ skip: 'unparsed-background', selector: 'div#lightbox-footer' })];
  assert.strictEqual(checkTextContrastCoverage(unparsed, waiver).length, 1,
    'an unparsed colour is not waivable, even on the same selector');
}

// Every state is judged on its own.
{
  const f = checkTextContrastCoverage([
    ...Array.from({ length: 9 }, (_, i) => rec({ state: 'dark/lightbox', selector: 'p.a' + i, ratio: 9 })),
    rec({ state: 'dark/timeline', skip: 'background-url-image' }),
  ]);
  assert.strictEqual(f.length, 1, 'only the failing state is reported');
  assert.strictEqual(f[0].state, 'dark/timeline', 'the finding must say which state is vacuous');
}

// summarise() must carry the histogram so the coverage line and the JSON report
// both show it, and summariseCoverage() must agree with the gate.
{
  const text = [
    rec({ ratio: 4.21, state: 'light/timeline' }),
    rec({ skip: 'background-url-image', state: 'light/timeline' }),
    rec({ skip: 'background-url-image', state: 'light/timeline' }),
    rec({ skip: 'unparsed-color', state: 'light/timeline' }),
  ];
  const sum = summarise({ targets: [], text, images: [] });
  assert.deepStrictEqual(sum.skipReasons, { 'background-url-image': 2, 'unparsed-color': 1 },
    'summarise() must include a per-reason skip histogram');
  assert.strictEqual(sum.textChecked, 1);
  assert.strictEqual(sum.textSkipped, 3);
  assert.strictEqual(sum.minContrast, 4.21);
  const agg = summariseCoverage(text, SKIP_EXCEPTIONS);
  assert.strictEqual(agg.textNodes, 4);
  assert.strictEqual(agg.textChecked, 1);
  assert.strictEqual(agg.textWaived, 0, 'the shipped exception list is empty, so nothing is waived');
  assert.strictEqual(agg.coverage, 0.25);
}

// withRatios() must collapse a gradient record to its worst-case pair, so the
// message, the JSON and the coverage line all quote one number.
{
  const grads = withRatios([rec({
    fg: '#8a8a8a',
    bgVariants: inPageBgVariants(OVER(138, 138, 138), 1, [OVER(255, 255, 255), OVER(0, 0, 0)]),
  })]);
  assert.strictEqual(grads[0].ratio.toFixed(2), '3.45', 'the worst gradient stop wins (3.45:1 on white)');
  assert.strictEqual(grads[0].bg, '#ffffff', 'the collapsed record must quote the failing background');
  assert.strictEqual(checkTextContrast(grads).length, 1,
    'the gradient text must fail the contrast assertion, not be skipped');
  assert.strictEqual(checkTextContrast([rec({ skip: 'background-url-image' })]).length, 0,
    'a url()-photo skip is still only a coverage problem');
}

// ---------------------------------------------------------------------------
// 5. The themes and the registry.
// ---------------------------------------------------------------------------
assert.deepStrictEqual(resolveThemes(undefined), ['light', 'dark'], 'the default is BOTH themes');
assert.deepStrictEqual(resolveThemes('both'), ['light', 'dark']);
assert.deepStrictEqual(resolveThemes('light'), ['light']);
assert.deepStrictEqual(resolveThemes('dark'), ['dark']);
assert.strictEqual(resolveThemes('bogus'), null, 'an unknown theme must be refused, not ignored');
assert.deepStrictEqual(THRESHOLDS.themes, ['light', 'dark'], 'Light and Dark are both audited');
assert.strictEqual(THRESHOLDS.themeStorageKey, 'funes-vision.theme',
  'the seeded key must be the one index.html reads');

// checkThemeApplied: a page that resolved the wrong theme must fail.
{
  const f = checkThemeApplied([{ state: 'theme/dark', theme: 'dark', applied: 'light' }]);
  assert.strictEqual(f.length, 1, 'a mismatched theme must fail');
  assert.strictEqual(f[0].check, 'theme-applied');
  assert.ok(/resolved "light"/.test(f[0].detail), 'the finding must say what the page resolved');
  assert.strictEqual(checkThemeApplied([{ state: 'theme/dark', theme: 'dark', applied: 'dark' }]).length, 0,
    'a matching theme must pass');
}

// The registry keeps the coverage family and the theme family, and every
// family still carries a what/why so a failure is explainable.
const ids = CHECKS.map((c) => c.id);
assert.ok(ids.includes('text-contrast-coverage'), 'the coverage gate must be registered');
assert.ok(ids.includes('theme-applied'), 'the theme gate must be registered');
assert.ok(ids.indexOf('text-contrast') < ids.indexOf('text-contrast-coverage'),
  'the contrast check and its coverage gate must both be there');
for (const c of CHECKS) {
  assert.strictEqual(typeof c.run, 'function', `${c.id} needs a runner`);
  assert.ok(c.what && c.why, `${c.id} needs a what/why so a failure is explainable`);
}
const byId = Object.fromEntries(CHECKS.map((c) => [c.id, c]));
assert.ok(/80%/.test(byId['text-contrast-coverage'].what),
  'the coverage check must spell out its floor in what');
assert.ok(/data-theme/.test(byId['theme-applied'].what), 'the theme check must name html[data-theme]');
assert.strictEqual(new Set(ids).size, ids.length, 'duplicate check id');

// ---------------------------------------------------------------------------
// 6. The two WCAG 1.4.3 exemptions (a disabled control's label, and text
//    layered over a picture). The collector only ever runs inside a browser,
//    so this is the in-page proof: hand the tiny fake document below to the
//    SAME init script a page gets from inPageBundleSource(), together with the
//    three globals it needs. A helper missing from that list would throw a
//    ReferenceError here instead of returning records.
// ---------------------------------------------------------------------------
const DEFAULTS = {
  display: 'block', visibility: 'visible', opacity: '1', position: 'static',
  fontSize: '16px', fontWeight: '400', color: 'rgb(0, 0, 0)',
  backgroundImage: 'none', backgroundColor: 'rgb(255, 255, 255)',
  textIndent: '0px',
};
const FAINT = { color: 'rgb(138, 138, 138)', backgroundColor: 'rgb(255, 255, 255)' };

// A fake element. `rect` doubles as "this element paints a box": null means no
// client rects at all. `disabled` is the DOM property, not the attribute.
function el(tag, opts) {
  const o = opts || {};
  const e = {
    nodeType: 1,
    tagName: tag.toUpperCase(),
    id: o.id || '',
    parentElement: null,
    childNodes: [],
    children: [],
    attrs: Object.assign({}, o.attrs),
    disabled: !!o.disabled,
    rect: o.rect === undefined ? { left: 0, top: 0, width: 100, height: 20 } : o.rect,
    style: Object.assign({}, DEFAULTS, o.style),
    getAttribute(name) {
      if (name === 'class') return e.attrs.class || null;
      return name in e.attrs ? e.attrs[name] : null;
    },
    contains(other) {
      for (let p = other; p; p = p.parentElement) if (p === e) return true;
      return false;
    },
    // Only the three selector forms the audit's own helpers use: a tag, [attr]
    // and [attr="value"] (see inPageCollectTargets and the exemption helpers).
    closest(sel) {
      const parts = String(sel).split(',').map((s) => s.trim());
      for (let n = e; n && n.nodeType === 1; n = n.parentElement) {
        if (parts.some((p) => fakeMatches(n, p))) return n;
      }
      return null;
    },
    getClientRects() { return e.rect ? [{ left: e.rect.left, top: e.rect.top }] : []; },
    getBoundingClientRect() {
      const r = e.rect || { left: 0, top: 0, width: 0, height: 0 };
      return {
        left: r.left, top: r.top, width: r.width, height: r.height,
        right: r.left + r.width, bottom: r.top + r.height,
      };
    },
  };
  return e;
}

function fakeText(value) {
  return { nodeType: 3, nodeValue: value, parentElement: null, childNodes: [], children: [] };
}

function fakeInto(parent, ...kids) {
  for (const k of kids) {
    k.parentElement = parent;
    parent.childNodes.push(k);
    if (k.nodeType === 1) parent.children.push(k);
  }
}

function fakeMatches(node, sel) {
  let m = sel.match(/^\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'))?\]$/);
  if (m) {
    const want = m[2] !== undefined ? m[2] : m[3];
    const have = node.getAttribute(m[1]);
    return want === undefined ? have !== null : have === want;
  }
  m = sel.match(/^([a-zA-Z][\w-]*)$/);
  return m ? node.tagName === m[1].toUpperCase() : false;
}

// Enough document for inPageCollectText(): an element tree, its text nodes in
// document order, a computed style per element and a querySelectorAll that
// finds the pictures.
function fakePage(body) {
  const html = el('html', { rect: { left: 0, top: 0, width: 1200, height: 900 } });
  fakeInto(html, body);
  return {
    documentElement: html,
    body,
    querySelectorAll(sel) {
      const wanted = String(sel).split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
      const out = [];
      const walk = (n) => {
        for (const c of n.childNodes) {
          if (c.nodeType !== 1) continue;
          if (wanted.indexOf(c.tagName.toLowerCase()) !== -1) out.push(c);
          walk(c);
        }
      };
      walk(html);
      return out;
    },
    createTreeWalker(root) {
      const texts = [];
      const walk = (n) => {
        for (const c of n.childNodes) {
          if (c.nodeType === 3) texts.push(c);
          else walk(c);
        }
      };
      walk(root);
      let i = 0;
      return { nextNode: () => (i < texts.length ? texts[i++] : null) };
    },
  };
}

const getComputedStyle = (e) => e.style;
const NodeFilter = { SHOW_TEXT: 4 };
// The bundle, compiled once with the three page globals as parameters. The
// return statement reaches back into the collected function declarations.
const inPage = new Function('document', 'getComputedStyle', 'NodeFilter', 'window',
  inPageBundleSource() + '\nreturn { collectText: inPageCollectText,'
    + ' disabledControl: inPageInsideDisabledControl, mediaShown: inPageMediaShown,'
    + ' overMedia: inPageOverMedia };');
const collectText = (body) => inPage(fakePage(body), getComputedStyle, NodeFilter, {}).collectText();

// The module-scope copies must be the same helpers the bundle ships, or the
// page would run code these tests never saw.
for (const fn of [inPageInsideDisabledControl, inPageMediaShown, inPageOverMedia]) {
  assert.strictEqual(typeof fn, 'function', 'the exemption helpers must be exported for tests');
}

// 6a. A disabled button's label: 1.4.3 exempts inactive UI components, and the
// node still comes back as a COUNTED skip rather than disappearing.
{
  const body = el('body');
  const btn = el('button', { id: 'disabled', disabled: true, style: FAINT });
  fakeInto(body, btn);
  fakeInto(btn, fakeText('Disabled label'));
  const recs = collectText(body);
  assert.strictEqual(recs.length, 1);
  assert.strictEqual(recs[0].skip, 'disabled-control',
    'a disabled button label must be a counted skip, not a measured finding');
  assert.strictEqual(recs[0].text, 'Disabled label',
    'the skip record carries the text like every other record');
  assert.ok(/button#disabled/.test(recs[0].selector),
    'the skip record carries the selector, so the histogram stays debuggable');
  assert.strictEqual(checkTextContrast(withRatios(recs)).length, 0,
    'a skip is not a contrast finding (only a coverage problem)');
  assert.strictEqual(
    inPage(fakePage(body), getComputedStyle, NodeFilter, {}).disabledControl(btn), true,
    'the helper itself must name the disabled control');
}

// 6b. The ENABLED twin of that button is still measured and still reported.
{
  const body = el('body');
  const btn = el('button', { id: 'enabled', style: FAINT });
  fakeInto(body, btn);
  fakeInto(btn, fakeText('Enabled label'));
  const recs = withRatios(collectText(body));
  assert.strictEqual(recs.length, 1);
  assert.strictEqual(recs[0].skip, undefined,
    'an ENABLED button is not an inactive component: it must be measured');
  assert.strictEqual(recs[0].ratio.toFixed(2), '3.45', 'the faint pair still measures 3.45:1');
  const findings = checkTextContrast(recs);
  assert.strictEqual(findings.length, 1, 'the enabled twin must still be reported');
  assert.ok(/button#enabled/.test(findings[0].selector));
  assert.strictEqual(
    inPage(fakePage(body), getComputedStyle, NodeFilter, {}).disabledControl(btn), false,
    'the helper must not fire on an enabled control');
}

// 6c. aria-disabled says the same thing as the property, on the control or on
// any ancestor, and both must be exempted.
{
  const body = el('body');
  const wrap = el('div', { id: 'group', attrs: { 'aria-disabled': 'true', role: 'group' } });
  const span = el('span', { id: 'label', style: FAINT });
  fakeInto(body, wrap);
  fakeInto(wrap, span);
  fakeInto(span, fakeText('aria disabled label'));
  const recs = collectText(body);
  assert.strictEqual(recs.length, 1);
  assert.strictEqual(recs[0].skip, 'disabled-control',
    'aria-disabled="true" on an ancestor must exempt the label inside it');
}

// 6d. Text layered over a picture: the classic photo caption.
{
  const body = el('body');
  const wrap = el('div', { id: 'thumb', style: { position: 'relative' },
    rect: { left: 0, top: 0, width: 220, height: 120 } });
  const img = el('img', { id: 'photo', attrs: { alt: 'x' },
    rect: { left: 0, top: 0, width: 220, height: 120 } });
  const cap = el('p', { id: 'cap', style: Object.assign({ position: 'absolute' }, FAINT),
    rect: { left: 0, top: 0, width: 220, height: 20 } });
  fakeInto(body, wrap);
  fakeInto(wrap, img, cap);
  fakeInto(cap, fakeText('Caption on the photo'));
  const recs = collectText(body);
  assert.strictEqual(recs.length, 1);
  assert.strictEqual(recs[0].skip, 'over-image',
    'an overlay caption on a photo must be a counted skip');
  const helpers = inPage(fakePage(body), getComputedStyle, NodeFilter, {});
  assert.strictEqual(helpers.overMedia([img], cap, cap.getBoundingClientRect()), true,
    'the helper must recognise the overlay');
  assert.strictEqual(helpers.overMedia([img], wrap, wrap.getBoundingClientRect()), false,
    'a wrapper that CONTAINS the picture is not text over it and must stay measured');
}

// 6d-bis. The shape the real app actually ships: the caption is TWO levels
// inside .card-overlay{position:absolute}, the thumbnail is a SIBLING of that
// overlay, and their common ancestor .image-card is only position:relative.
// A rule that demands the positioned ancestor itself hold the image matches
// nothing here, so every caption on the timeline would be measured against
// pixels it is painted over -- and the coverage floor would eat the difference.
{
  const body = el('body');
  const card = el('div', { id: 'card', attrs: { class: 'image-card' },
    style: { position: 'relative' }, rect: { left: 0, top: 0, width: 220, height: 120 } });
  const img = el('img', { id: 'thumb', attrs: { alt: 'x' },
    rect: { left: 0, top: 0, width: 220, height: 120 } });
  const overlay = el('div', { id: 'overlay', attrs: { class: 'card-overlay' },
    style: { position: 'absolute' }, rect: { left: 0, top: 0, width: 220, height: 40 } });
  const meta = el('div', { id: 'meta', attrs: { class: 'meta-info' },
    rect: { left: 0, top: 0, width: 220, height: 40 } });
  const span = el('span', { id: 'when', style: FAINT,
    rect: { left: 0, top: 0, width: 60, height: 20 } });
  fakeInto(body, card);
  fakeInto(card, img, overlay);
  fakeInto(overlay, meta);
  fakeInto(meta, span);
  fakeInto(span, fakeText('2 minutes ago'));
  const recs = collectText(body);
  assert.strictEqual(recs.length, 1);
  assert.strictEqual(recs[0].skip, 'over-image',
    'a caption inside an absolutely-positioned overlay beside the thumbnail must be exempt');
  assert.ok(/span#when/.test(recs[0].selector), 'the skip record must name the caption');
  const helpers = inPage(fakePage(body), getComputedStyle, NodeFilter, {});
  assert.strictEqual(helpers.overMedia([img], span, span.getBoundingClientRect()), true,
    'the helper must walk the overlay stack up to the branch point');
  // The relative card that holds BOTH the image and the caption is the branch
  // point, so it is never tested: a wrapper is not an overlay.
  assert.strictEqual(helpers.overMedia([img], card, card.getBoundingClientRect()), false,
    'the common ancestor of the caption and the picture is not an overlay');
  // The overlay itself is a positioned box beside the picture, so it qualifies
  // too -- which is what makes the caption inside it exempt.
  assert.strictEqual(helpers.overMedia([img], overlay, overlay.getBoundingClientRect()), true,
    'the absolutely-positioned overlay is itself text-shaped and qualifies');
}

// 6d-ter. A static, non-overlay paragraph beside the picture has no
// absolute/fixed anywhere between it and the branch point, so the picture is
// merely a neighbour: the text must still be measured.
{
  const body = el('body');
  const wrap = el('div', { id: 'thumb', style: { position: 'relative' },
    rect: { left: 0, top: 0, width: 220, height: 120 } });
  const img = el('img', { id: 'photo', attrs: { alt: 'x' },
    rect: { left: 0, top: 0, width: 220, height: 120 } });
  // Sits OUTSIDE the wrapper, in ordinary flow, beside the picture.
  const note = el('div', { id: 'note', rect: { left: 0, top: 0, width: 220, height: 20 } });
  const p = el('p', { id: 'beside', style: FAINT, rect: { left: 0, top: 0, width: 220, height: 20 } });
  fakeInto(body, wrap, note);
  fakeInto(wrap, img);
  fakeInto(note, p);
  fakeInto(p, fakeText('a paragraph beside the picture'));
  const recs = withRatios(collectText(body));
  assert.strictEqual(recs.length, 1);
  assert.strictEqual(recs[0].skip, undefined,
    'a static paragraph beside the picture must still be measured');
  const findings = checkTextContrast(recs);
  assert.strictEqual(findings.length, 1,
    'the faint paragraph beside the picture must still be reported');
  assert.ok(/p#beside/.test(findings[0].selector));
  const helpers = inPage(fakePage(body), getComputedStyle, NodeFilter, {});
  assert.strictEqual(helpers.overMedia([img], p, p.getBoundingClientRect()), false,
    'no positioned layer below the branch point means no overlay');
}

// 6e. The same faint text with NO picture under it is still measured.
{
  const body = el('body');
  const p = el('p', { id: 'plain', style: FAINT, rect: { left: 0, top: 0, width: 300, height: 20 } });
  fakeInto(body, p);
  fakeInto(p, fakeText('caption with no picture'));
  const findings = checkTextContrast(withRatios(collectText(body)));
  assert.strictEqual(findings.length, 1,
    'the same faint text with no image behind it must still be reported');
  assert.ok(/p#plain/.test(findings[0].selector));
}

// 6f. Conservative: the picture must actually be behind the TEXT's centre.
{
  const body = el('body');
  const img = el('img', { id: 'photo', attrs: { alt: 'x' },
    rect: { left: 0, top: 0, width: 220, height: 120 } });
  const p = el('p', { id: 'below', style: Object.assign({ position: 'absolute' }, FAINT),
    rect: { left: 0, top: 200, width: 300, height: 20 } });
  fakeInto(body, img, p);
  fakeInto(p, fakeText('text below the photo'));
  const recs = collectText(body);
  assert.strictEqual(recs.length, 1);
  assert.strictEqual(recs[0].skip, undefined,
    'text whose centre is outside the picture must be measured');
  assert.strictEqual(
    inPage(fakePage(body), getComputedStyle, NodeFilter, {}).overMedia([img], p, p.getBoundingClientRect()),
    false, 'the helper must refuse a picture the text centre is not over');
}

// 6g. Conservative: a picture inside the text element is layout, not an overlay
// (an inline image in a paragraph), even when the text is positioned.
{
  const body = el('body');
  const p = el('p', { id: 'para', style: Object.assign({ position: 'absolute' }, FAINT),
    rect: { left: 0, top: 0, width: 300, height: 200 } });
  const img = el('img', { id: 'inline', attrs: { alt: 'x' },
    rect: { left: 0, top: 0, width: 200, height: 200 } });
  fakeInto(body, p);
  fakeInto(p, img, fakeText('caption beside the image'));
  const recs = collectText(body);
  assert.strictEqual(recs.length, 1);
  assert.strictEqual(recs[0].skip, undefined,
    'an <img> inside the text element is its own content, not an overlay');
}

// 6h. Conservative: text that lives INSIDE a <video> is that element's fallback
// content, and the picture is its ancestor -- not something it is layered over.
{
  const body = el('body');
  const vid = el('video', { id: 'clip', rect: { left: 0, top: 0, width: 320, height: 180 } });
  const p = el('p', { id: 'fallback', style: Object.assign({ position: 'absolute' }, FAINT),
    rect: { left: 0, top: 0, width: 200, height: 20 } });
  fakeInto(body, vid);
  fakeInto(vid, p);
  fakeInto(p, fakeText('Your browser cannot play this clip'));
  const recs = collectText(body);
  assert.strictEqual(recs.length, 1);
  assert.strictEqual(recs[0].skip, undefined,
    'text inside a <video> is that element\'s own content, not an overlay');
}

// 6i. Conservative: a picture that is not really painted (display:none
// ancestor) is not something the text is layered over.
{
  const body = el('body');
  const hidden = el('div', { id: 'hidden-wrap', style: { display: 'none' },
    rect: { left: 0, top: 0, width: 220, height: 120 } });
  const img = el('img', { id: 'ghost', attrs: { alt: 'x' },
    rect: { left: 0, top: 0, width: 220, height: 120 } });
  const cap = el('p', { id: 'cap', style: Object.assign({ position: 'absolute' }, FAINT),
    rect: { left: 0, top: 0, width: 220, height: 20 } });
  fakeInto(body, hidden, cap);
  fakeInto(hidden, img);
  fakeInto(cap, fakeText('caption over an unpainted picture'));
  const recs = collectText(body);
  assert.strictEqual(recs.length, 1);
  assert.strictEqual(recs[0].skip, undefined,
    'an unpainted picture must not exempt the text in front of it');
  assert.strictEqual(
    inPage(fakePage(body), getComputedStyle, NodeFilter, {}).mediaShown(img), false,
    'the helper must see a display:none ancestor as not painted');
}

// 6j. Both reasons are COUNTED: they land in the histogram, in the coverage
// denominator, and they are never contrast findings.
{
  const exempt = [
    rec({ skip: 'disabled-control', selector: 'button#a' }),
    rec({ skip: 'over-image', selector: 'p#cap' }),
  ];
  const measured = Array.from({ length: 12 }, () => rec({ ratio: 12 }));
  const all = measured.concat(exempt);
  assert.deepStrictEqual(summarise({ targets: [], text: all, images: [] }).skipReasons,
    { 'disabled-control': 1, 'over-image': 1 },
    'both new reasons must show up in the skip-reason histogram');
  assert.deepStrictEqual(summarise({ targets: [], text: exempt, images: [] }).skipReasons,
    { 'disabled-control': 1, 'over-image': 1 });
  const agg = summariseCoverage(all, SKIP_EXCEPTIONS);
  assert.strictEqual(agg.textNodes, 14, 'every node is still counted as a text node');
  assert.strictEqual(agg.textSkipped, 2);
  assert.strictEqual(agg.textChecked, 12);
  assert.strictEqual(agg.textExempt, 1,
    'the disabled-control node is EXEMPT by 1.4.3, so it leaves the denominator');
  assert.strictEqual(agg.coverage, 12 / 13,
    'the denominator is the 14 nodes minus the exempt one, not all 14');
  assert.strictEqual(checkTextContrastCoverage(all).length, 0,
    '12 of 13 measurable clears the 80% floor');
  assert.strictEqual(checkTextContrast(all).length, 0,
    'an exempt node is a coverage problem, never a contrast finding');
}

// 6k. The denominator itself: WCAG 1.4.3 exempts the label of an inactive
// control, so those nodes must not drag coverage down -- while an unmeasurable
// node (text over a picture) still does.
{
  // 100 nodes, 40 measured, 60 disabled-control: 40 of 40 measurable = 100%.
  {
    const recs = Array.from({ length: 100 }, (_, i) => (i < 40
      ? rec({ ratio: 12, selector: 'p.m' + i })
      : rec({ skip: 'disabled-control', selector: 'button#d' + i })));
    const agg = summariseCoverage(recs, SKIP_EXCEPTIONS);
    assert.strictEqual(agg.textNodes, 100);
    assert.strictEqual(agg.textChecked, 40);
    assert.strictEqual(agg.textExempt, 60, 'the 60 disabled nodes are exempt, not unmeasured');
    assert.strictEqual(agg.skipReasons['disabled-control'], 60,
      'the exempt count must still be reported in the histogram');
    assert.strictEqual(agg.coverage, 1, '40 of 40 measurable is 100% coverage');
    assert.strictEqual(checkTextContrastCoverage(recs).length, 0,
      'exempt disabled labels must not drag the state below the floor');
  }
  // 100 nodes, 0 measured, 100 disabled-control: an empty denominator is a
  // FAIL, never a vacuous 100%.
  {
    const recs = Array.from({ length: 100 },
      (_, i) => rec({ skip: 'disabled-control', selector: 'button#d' + i }));
    const agg = summariseCoverage(recs, SKIP_EXCEPTIONS);
    assert.strictEqual(agg.textChecked, 0);
    assert.strictEqual(agg.textExempt, 100);
    assert.strictEqual(agg.coverage, 0, 'an empty denominator must be 0% coverage, never vacuous');
    const f = checkTextContrastCoverage(recs);
    assert.strictEqual(f.length, 1, 'a state of nothing but exempt nodes must FAIL');
    assert.ok(/measured 0 of 100/.test(f[0].detail), 'the finding must carry the numbers');
    assert.ok(/exempt-disabled: 100/.test(f[0].detail),
      'the finding must print the exempt-disabled count next to the coverage figure');
    assert.ok(/disabled-control:100/.test(f[0].detail),
      'the finding must still carry the skip-reason histogram');
  }
  // 100 nodes, 70 measured, 30 over-image: over-image is NOT exempt, so the
  // denominator is still 100 and 70% fails the 80% floor.
  {
    const recs = Array.from({ length: 100 }, (_, i) => (i < 70
      ? rec({ ratio: 12, selector: 'p.m' + i })
      : rec({ skip: 'over-image', selector: 'p.o' + i })));
    const agg = summariseCoverage(recs, SKIP_EXCEPTIONS);
    assert.strictEqual(agg.textExempt, 0, 'an over-image node is unmeasurable, not exempt');
    assert.strictEqual(agg.coverage, 0.7);
    const f = checkTextContrastCoverage(recs);
    assert.strictEqual(f.length, 1, '70 of 100 measurable must fail the 0.8 floor');
    assert.ok(/70% coverage/.test(f[0].detail), 'the finding must quote the coverage');
    assert.ok(/exempt-disabled: 0/.test(f[0].detail),
      'the finding must print the exempt-disabled count even when it is zero');
    assert.ok(/of 100 text node/.test(f[0].detail), 'the denominator must be the measurable nodes');
  }
}

// 6l. The per-state summary the JSON report and the theme line are built from
// must use the same denominator as the gate, or the printed coverage figure and
// the gate's verdict would disagree.
{
  const text = [
    ...Array.from({ length: 8 }, () => rec({ ratio: 12, state: 'light/timeline' })),
    ...Array.from({ length: 2 }, (_, i) => rec({ skip: 'disabled-control', selector: 'button#d' + i, state: 'light/timeline' })),
  ];
  const sum = summarise({ targets: [], text, images: [] });
  assert.strictEqual(sum.textNodes, 10);
  assert.strictEqual(sum.textExempt, 2);
  assert.strictEqual(sum.coverage, 1, '8 of 8 measurable is 100%');
  // A state with no disabled nodes keeps the plain ratio.
  const plain = summarise({ targets: [], text: Array.from({ length: 4 }, () => rec({ ratio: 12 })), images: [] });
  assert.strictEqual(plain.coverage, 1);
  const half = summarise({
    targets: [],
    text: [rec({ ratio: 12 }), rec({ skip: 'background-url-image', selector: 'p.x' })],
    images: [],
  });
  assert.strictEqual(half.coverage, 0.5, 'a url()-photo skip is still in the denominator');
  // And the gate agrees with the summary.
  assert.strictEqual(checkTextContrastCoverage(text).length, 0,
    'the gate must accept what the per-state summary calls 100%');
}

// The in-page bundle must ship the gradient helpers, or the browser would
// still skip every node (and this test suite cannot see that from Node: the
// collector runs in the page).
const bundle = inPageBundleSource();
for (const fn of ['inPageGradientStops', 'inPageBgImageLayers', 'inPageBgLayer',
  'inPagePaintLayers', 'inPageCompositeLayer', 'inPageBgVariants',
  'inPageInsideDisabledControl', 'inPageMediaShown', 'inPageOverMedia']) {
  assert.ok(bundle.indexOf('function ' + fn + '(') !== -1, `the page bundle must define ${fn}`);
}
assert.ok(!/module\.exports|require\(/.test(bundle), 'the bundle must not depend on module scope');

console.log('a11y-coverage-gate: all assertions passed');
