// Contract test for tools/screenshots/a11y_audit.js (the headless-browser
// a11y/visual gate). The gate itself needs Chromium, so this suite pins the
// part that must not rot silently: the WCAG thresholds, the check registry,
// and -- most importantly -- that each check actually FAILS on a seeded
// violation and PASSES on a compliant record. Without this, someone could
// empty CHECKS, or raise minTargetPx to 999, and the gate would stay green.
//
// The browser-backed half of the same proof lives in the script's --selfcheck
// mode, which CI runs in the browser-a11y-gate job.
//
// Run: node tests/test_a11y_audit_gate.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const audit = require(path.join(__dirname, '..', 'tools', 'screenshots', 'a11y_audit.js'));
const { THRESHOLDS, CHECKS, PAGE_CFG, checkHttpClean, checkTargetSize, checkTextContrast, checkOverflow, checkReducedMotion, checkImages, contrastRatio, imageBroken } = audit;

// ---------------------------------------------------------------------------
// 1. The thresholds are the WCAG numbers. A gate that passes because its
//    limits were raised is worse than no gate at all.
// ---------------------------------------------------------------------------
assert.strictEqual(THRESHOLDS.minTargetPx, 24, 'WCAG 2.5.8 AA target size floor is 24x24 CSS px');
assert.strictEqual(THRESHOLDS.contrastBody, 4.5, 'WCAG 1.4.3 AA body-text contrast is 4.5:1');
assert.strictEqual(THRESHOLDS.contrastLarge, 3, 'WCAG 1.4.3 AA large-text contrast is 3:1');
assert.strictEqual(THRESHOLDS.largeTextPx, 24, 'WCAG 1.4.3 large scale starts at 18pt = 24px');
assert.strictEqual(THRESHOLDS.largeBoldPx, 18.66, 'WCAG 1.4.3 bold large scale starts at 14pt = 18.66px');
assert.strictEqual(THRESHOLDS.boldWeight, 700, 'WCAG 1.4.3 "bold" is weight 700+');
assert.deepStrictEqual([...THRESHOLDS.overflowWidths], [320, 360, 390, 414, 768, 1024, 1280, 1440],
  'the gate must sweep the real phone/tablet/desktop widths');
assert.ok(THRESHOLDS.reducedMotionMs > 0 && THRESHOLDS.reducedMotionMs <= 100,
  'the reduced-motion threshold must be a real, small duration, not a no-op (50ms)');
assert.ok(THRESHOLDS.reducedMotionMs < 1000,
  'the reduced-motion threshold must not swallow a real 700ms animation');
assert.ok(THRESHOLDS.forbiddenRoots.includes('/mnt/models'),
  'the gate must refuse to run against a live camera root');
assert.deepStrictEqual(Object.keys(PAGE_CFG).sort(),
  ['boldWeight', 'invisibleOpacity', 'largeBoldPx', 'largeTextPx', 'overflowTolerancePx'].sort(),
  'the in-page config must stay in step with THRESHOLDS (it is generated from it)');
for (const k of ['largeTextPx', 'largeBoldPx', 'boldWeight', 'overflowTolerancePx']) {
  assert.strictEqual(PAGE_CFG[k], THRESHOLDS[k], `in-page CFG.${k} must equal THRESHOLDS.${k}`);
}
assert.ok(PAGE_CFG.invisibleOpacity > 0 && PAGE_CFG.invisibleOpacity <= 0.25,
  'the "not perceivable" opacity floor must stay small, or dim labels slip through');

// ---------------------------------------------------------------------------
// 2. The check registry: exactly the six assertion families, each with an id,
//    a human description and a runner.
// ---------------------------------------------------------------------------
assert.deepStrictEqual(CHECKS.map((c) => c.id), [
  'http-clean', 'target-size', 'text-contrast', 'h-overflow', 'reduced-motion', 'image-alt',
], 'the gate must keep all six assertion families');
for (const c of CHECKS) {
  assert.strictEqual(typeof c.run, 'function', `${c.id} needs a runner`);
  assert.ok(c.what && c.why, `${c.id} needs a what/why so a failure is explainable`);
}
// Nothing may be registered twice: a duplicated id would double-count findings.
assert.strictEqual(new Set(CHECKS.map((c) => c.id)).size, CHECKS.length, 'duplicate check id');

// ---------------------------------------------------------------------------
// 3. Each check must FAIL on a seeded violation and PASS on a compliant one.
// ---------------------------------------------------------------------------

// -- http-clean ---------------------------------------------------------
const cleanNet = [
  { state: 'timeline', url: '/api/cameras', status: 200 },
  { state: 'timeline', url: '/index.html', status: 200 },
];
assert.deepStrictEqual(checkHttpClean(cleanNet), [], 'a clean load must not fail http-clean');
const dirtyNet = [
  { state: 'timeline', url: '/api/taxonomy', status: 404 },
  { state: 'motion', url: '/thumbs/x.jpg', status: 500 },
  { state: 'status', url: '/api/status', failed: true, failure: 'net::ERR_CONNECTION_REFUSED' },
  { state: 'status', url: 'pageerror', pageError: 'TypeError: x is not a function' },
  { state: 'settings', url: 'console', console: 'Uncaught TypeError', consoleSource: 'http://127.0.0.1:8899/' },
];
const netFindings = checkHttpClean(dirtyNet);
assert.strictEqual(netFindings.length, 5, 'every 4xx/5xx, refused request, page error and console error must fail');
for (const f of netFindings) {
  assert.ok(/HTTP (404|500)|request failed|uncaught:|console error/.test(f.detail),
    `http-clean finding must state the measurement, got: ${f.detail}`);
  assert.ok(f.selector, 'http-clean finding must name the URL');
}

// -- target-size (WCAG 2.5.8) -------------------------------------------
const compliantTarget = {
  state: 'motion', selector: 'button#btn-filters', tag: 'button', name: 'Filters',
  w: 40, h: 40, rendered: true, visibility: 'visible', opacity: 1,
  pointerEvents: 'auto', ariaHidden: false, inlineInText: false, fullWidth: false,
};
assert.deepStrictEqual(checkTargetSize([compliantTarget]), [], 'a 40x40 button must pass 2.5.8');
const smallTarget = Object.assign({}, compliantTarget, { selector: '.chart-hit', w: 6, h: 5 });
const targetFindings = checkTargetSize([smallTarget]);
assert.strictEqual(targetFindings.length, 1, 'a 6x5 target must fail 2.5.8');
assert.ok(/measured 6x5px/.test(targetFindings[0].detail), 'the finding must carry the measurement');
assert.ok(/24x24px/.test(targetFindings[0].detail), 'the finding must carry the threshold');
// A 0x0 focusable is the class of bug that shipped (a closed Motion panel).
assert.strictEqual(checkTargetSize([Object.assign({}, smallTarget, { w: 0, h: 0 })]).length, 1,
  'a 0x0 rendered focusable must fail');
// The documented carve-outs must still be honoured, or the gate is useless.
assert.deepStrictEqual(checkTargetSize([Object.assign({}, smallTarget, { rendered: false })]), [],
  'an element with no layout box is not a target');
assert.deepStrictEqual(checkTargetSize([Object.assign({}, smallTarget, { visibility: 'hidden' })]), [],
  'a visibility:hidden target is not a target');
assert.deepStrictEqual(checkTargetSize([Object.assign({}, smallTarget, { opacity: 0 })]), [],
  'a fully transparent target is not a target');
assert.deepStrictEqual(checkTargetSize([Object.assign({}, smallTarget, { ariaHidden: true })]), [],
  'an aria-hidden target is not a target');
assert.deepStrictEqual(checkTargetSize([Object.assign({}, smallTarget, { pointerEvents: 'none' })]), [],
  'a pointer-events:none target is not a target');
assert.deepStrictEqual(checkTargetSize([Object.assign({}, smallTarget, { inlineInText: true })]), [],
  'WCAG 2.5.8 exempts a link in a run of text');
assert.deepStrictEqual(checkTargetSize([Object.assign({}, smallTarget, { fullWidth: true, w: 1200, h: 40 })]), [],
  'a full-width, tall-enough control is exempt on the width axis');
assert.strictEqual(checkTargetSize([Object.assign({}, smallTarget, { fullWidth: true, w: 1200, h: 5 })]).length, 1,
  'the full-width carve-out must not exempt the height axis -- a 5px-tall row is still unusable');

// -- text-contrast (WCAG 1.4.3) -----------------------------------------
const mkText = (fg, bg, over) => Object.assign({
  state: 'status', selector: 'span.hint', text: 'Parcel or postie?',
  fontSize: 10.88, fontWeight: 400, large: false, fg, bg, fgRgb: [255, 255, 255], bgRgb: [0, 0, 0],
}, over);
// #94a3b8 on #1e293b is 5.54:1 (the app's --text-secondary on --bg-tertiary).
assert.deepStrictEqual(checkTextContrast([mkText('#94a3b8', '#1e293b', { fgRgb: [148, 163, 184], bgRgb: [30, 41, 59] })]), [],
  '--text-secondary on --bg-tertiary is 5.54:1 and must pass');
// The same colour at the 0.8 opacity index.html:7789 applies is 4.21:1.
const dimmed = mkText('#7c8b9f', '#1e293b', { fgRgb: [124, 139, 159], bgRgb: [30, 41, 59] });
const contrastFindings = checkTextContrast([dimmed]);
assert.strictEqual(contrastFindings.length, 1, 'a 4.21:1 body-text pair must fail 1.4.3');
assert.ok(/measured 4\.21:1/.test(contrastFindings[0].detail), 'the finding must carry the ratio');
assert.ok(/required 4\.5:1/.test(contrastFindings[0].detail), 'the finding must carry the threshold');
assert.deepStrictEqual(checkTextContrast([Object.assign({}, dimmed, { large: true })]), [],
  'the same pair at large-text scale passes the 3:1 floor');
assert.deepStrictEqual(checkTextContrast([Object.assign({}, dimmed, { skip: 'background-image' })]), [],
  'a skipped record (e.g. text over a gradient) must not be reported as a failure');
// The WCAG formula itself, against the canonical black/white and mid-grey pairs.
assert.strictEqual(contrastRatio([0, 0, 0], [255, 255, 255]).toFixed(2), '21.00');
assert.strictEqual(contrastRatio([255, 255, 255], [255, 255, 255]).toFixed(2), '1.00');
assert.ok(Math.abs(contrastRatio([255, 255, 255], [0, 0, 0]) - 21) < 0.001, 'contrast is symmetric');
// #767676 on black is the canonical 4.62:1 mid-grey point.
assert.ok(Math.abs(contrastRatio([118, 118, 118], [0, 0, 0]) - 4.62) < 0.01,
  'the mid-grey reference point must hold, or the sRGB curve is wrong');
// Cross-check against the pair tests/test_a11y_contrast.js already documents:
// --accent (#3b82f6) on --bg-tertiary (#1e293b) is 3.98:1, which is the bug
// that test guards statically. The browser measurement must agree with it.
assert.ok(Math.abs(contrastRatio([0x3b, 0x82, 0xf6], [0x1e, 0x29, 0x3b]) - 3.98) < 0.01,
  'the measured ratio must agree with the repo\'s existing static a11y test');

// -- h-overflow ---------------------------------------------------------
const mkOverflow = (scrollWidth, clientWidth, offenders) => ({
  state: 'timeline', width: clientWidth, scrollWidth, clientWidth, scrollX: scrollWidth - clientWidth,
  offenders: offenders || [], offenderCount: (offenders || []).length,
});
assert.deepStrictEqual(checkOverflow([mkOverflow(320, 320)]), [], 'a page that fits must pass');
assert.deepStrictEqual(checkOverflow([mkOverflow(321, 320)]), [],
  '1px of sub-pixel rounding must be tolerated (tolerance 1px)');
const overflowFindings = checkOverflow([mkOverflow(330, 320, [{ selector: 'div#live-toggle', x: 267, right: 330, w: 63 }])]);
assert.strictEqual(overflowFindings.length, 1, 'a 10px sideways scroll at 320px must fail');
assert.ok(/#live-toggle/.test(overflowFindings[0].selector), 'the finding must name the widest offender');
assert.ok(/scrollWidth 330 > clientWidth 320/.test(overflowFindings[0].detail),
  'the finding must carry the measurement');

// -- reduced-motion -----------------------------------------------------
const mkMotion = (animations, scrollBehavior) => ({ state: 'timeline', scrollBehavior: scrollBehavior || 'auto', scrollSelector: 'html', animations });
assert.deepStrictEqual(checkReducedMotion([mkMotion([])]), [], 'no animations is a pass');
// The override index.html ships: 0.001ms, i.e. 1e-06s. A string compare against
// "0s" would call this motion; a numeric one must not.
assert.deepStrictEqual(checkReducedMotion([mkMotion([{ type: 'CSSAnimation', name: 'pulse', durationMs: 0.001, iterations: 1, selector: 'div.live-dot', visible: true }])]), [],
  'a 0.001ms animation under reduced motion is not motion');
const motionFindings = checkReducedMotion([mkMotion([
  { type: 'CSSAnimation', name: 'evp-spin', durationMs: 700, iterations: Infinity, selector: 'div.evp-spin', visible: true },
])]);
assert.strictEqual(motionFindings.length, 1, 'a 700ms infinite spin under reduced motion must fail');
assert.ok(/runs 700ms x infinite, required <= 50ms/.test(motionFindings[0].detail),
  'the finding must carry the measurement and the threshold');
assert.deepStrictEqual(checkReducedMotion([mkMotion([
  { type: 'CSSAnimation', name: 'evp-spin', durationMs: 700, iterations: Infinity, selector: 'div.evp-spin', visible: false },
])]), [], 'an animation in a hidden subtree is not visible motion');
assert.strictEqual(checkReducedMotion([mkMotion([], 'smooth')]).length, 1,
  'scroll-behavior: smooth under reduced motion must fail');

// -- image-alt ----------------------------------------------------------
const mkImg = (o) => Object.assign({
  state: 'motion', selector: 'div.image-card > img.card-img', tag: 'img',
  hasAlt: true, alt: 'Courier at the porch', shown: true, complete: true, naturalWidth: 320, src: 'thumbs/x.jpg',
}, o);
assert.deepStrictEqual(checkImages([mkImg({})]), [], 'a described, loaded image must pass');
const imgFindings = checkImages([mkImg({ hasAlt: false })]);
assert.strictEqual(imgFindings.length, 1, 'an <img> with no alt attribute must fail');
assert.ok(/no alt attribute/.test(imgFindings[0].detail), 'the finding must say what is missing');
assert.strictEqual(checkImages([mkImg({ naturalWidth: 0, complete: true })]).length, 1,
  'a rendered image that loaded as 0x0 is the blank-screen class and must fail');
assert.deepStrictEqual(checkImages([mkImg({ naturalWidth: 0, complete: true, alt: '' })]), [],
  'a decorative alt="" image is not a broken-content-image');
assert.deepStrictEqual(checkImages([mkImg({ naturalWidth: 0, complete: false })]), [],
  'a still-loading lazy image is not a broken image');
assert.deepStrictEqual(checkImages([mkImg({ naturalWidth: 0, shown: false })]), [],
  'a crossfaded-out or display:none image is not a broken image');
assert.strictEqual(imageBroken(mkImg({ naturalWidth: 0 })), true, 'imageBroken() is the shared predicate');
assert.strictEqual(imageBroken(mkImg({ naturalWidth: 0, shown: false })), false);

// ---------------------------------------------------------------------------
// 4. The in-page bundle must ship a collector for every check that needs one,
//    and must not depend on module scope (page.evaluate drops free variables,
//    which is why the collectors are shipped as an init script instead).
// ---------------------------------------------------------------------------
const bundle = audit.inPageBundleSource();
for (const fn of ['inPageCollectTargets', 'inPageCollectText', 'inPageCollectOverflow', 'inPageCollectMotion', 'inPageCollectImages']) {
  assert.ok(bundle.indexOf('function ' + fn + '(') !== -1, `the page bundle must define ${fn}`);
}
assert.ok(bundle.indexOf('window.__a11yAudit = {') !== -1, 'the bundle must publish its collectors on window');
assert.ok(bundle.indexOf(JSON.stringify(PAGE_CFG)) !== -1,
  'the bundle must be generated from PAGE_CFG, or the page measures with stale numbers');
for (const fn of ['checkHttpClean', 'checkTargetSize', 'checkTextContrast', 'checkOverflow', 'checkReducedMotion', 'checkImages']) {
  assert.strictEqual(typeof audit[fn], 'function', `${fn} must stay exported for the tests`);
}

// ---------------------------------------------------------------------------
// 5. The CI wiring. A gate nobody runs is a gate that does not exist, and a
//    gate whose step is allowed to fail is a gate that cannot report anything.
// ---------------------------------------------------------------------------
const repo = path.join(__dirname, '..');
const workflow = fs.readFileSync(path.join(repo, '.github/workflows/ci.yml'), 'utf8');
const devReqs = fs.readFileSync(path.join(repo, 'requirements-dev.txt'), 'utf8');
const runtimeReqs = fs.readFileSync(path.join(repo, 'requirements.txt'), 'utf8');

const pin = devReqs.match(/^# ?playwright==([0-9.]+)$/m);
assert.ok(pin, 'requirements-dev.txt must carry exactly one commented playwright pin');
// Anchored, like the grep CI uses: the prose above the pin quotes the pattern,
// and a non-anchored count would see both.
assert.strictEqual((devReqs.match(/^# ?playwright==[0-9.]+$/gm) || []).length, 1,
  'the playwright pin must appear once, so the grep CI uses is unambiguous');
assert.ok(devReqs.split('\n').includes(`# playwright==${pin[1]}`),
  'the pin must be a comment, or pip install -r requirements-dev.txt breaks');
assert.ok(!/^playwright/m.test(runtimeReqs) && !runtimeReqs.includes('playwright'),
  'playwright is a dev dependency and must never appear in requirements.txt');
assert.ok(devReqs.includes('-r requirements.txt'),
  'the dev file must pull in the runtime set for a dev checkout');

assert.ok(/^ {2}browser-a11y-gate:/m.test(workflow), 'CI must have a browser-a11y-gate job');
const jobBody = workflow.slice(workflow.indexOf('  browser-a11y-gate:'));
const testBody = workflow.slice(workflow.indexOf('  test:'), workflow.indexOf('  browser-a11y-gate:'));
assert.ok(!testBody.includes('a11y_audit'), 'the audit must not be in the fast `test` job');
assert.ok(jobBody.includes("grep -oE '^# ?playwright==[0-9.]+' requirements-dev.txt"),
  'the job must install the version pinned in requirements-dev.txt, not a hardcoded one');
assert.ok(jobBody.includes('npm install --no-save --no-package-lock "playwright@${{ steps.pw.outputs.version }}"'),
  'the job must install the resolved pin with --no-save (the repo has no package.json)');
assert.ok(jobBody.includes('npx playwright install --with-deps chromium'),
  'the job must install Chromium and its system libraries');
assert.ok(/actions\/cache@v4/.test(jobBody) && jobBody.includes('~/.cache/ms-playwright'),
  'the ~170MB browser must be cached, or every run re-downloads it');
assert.ok(jobBody.includes('a11y_audit.js --selfcheck'),
  'the job must run the non-vacuity self-check before trusting the gate');
assert.ok(jobBody.includes('tools/screenshots/proxy.py'),
  'the job must serve the audit from the synthetic fixture stub');
const auditStep = jobBody.slice(jobBody.indexOf('name: Audit the fixture gallery'));
assert.ok(!/continue-on-error/.test(auditStep) && !/\|\|\s*true/.test(auditStep),
  'the audit step must not be allowed to fail -- that would neuter the gate');
assert.ok(/A11Y_AUDIT_JSON/.test(jobBody) && /upload-artifact/.test(jobBody),
  'the machine-readable report must be uploaded even when the audit fails');

console.log('a11y-audit-gate: all assertions passed');
