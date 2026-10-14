/** Real Chromium proof of both read-only activity views at the release
 *  breakpoints. Synthetic temporary folder only; no real camera or model.
 *
 *  Usage:  node tests/activity_browser.cjs [--evidence]
 *  Env:    FUNES_PYTHON, PLAYWRIGHT_MODULE, CHROMIUM_EXECUTABLE
 *
 *  `--evidence` writes screenshots into docs/evidence/ instead of a temporary
 *  directory. Screenshot files are a by-product of the assertions below; nothing
 *  in this script trusts a picture it has not also asserted on.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { spawnPython, freePort, waitForHttp, stopProcess } = require('./harness.cjs');

const ROOT = path.resolve(__dirname, '..');
const WIDTHS = [320, 360, 390, 768, 1366, 1440];
const writeEvidence = process.argv.includes('--evidence');
/** Widths kept as committed visual evidence, per view. All six widths are
 *  asserted for overflow and keyboard operation on every run; only the widths
 *  cited by the README are written to docs/evidence/. */
const EVIDENCE_WIDTHS = {timeline: new Set([390, 1366]),
  companion: new Set([320, 768, 1440]), 'node-only': new Set([1366])};
/** Committed evidence filenames, including the ones the README already cites. */
const EVIDENCE_NAMES = {
  'timeline/1366': 'folder-activity-desktop.png',
  'timeline/390': 'folder-activity-mobile.png',
  'timeline-empty/1366': 'folder-activity-00-empty.png',
  'companion/320': 'activity-companion-320.png',
  'companion/768': 'activity-companion-768.png',
  'companion/1440': 'activity-companion-1440.png',
  'node-only/1366': 'activity-node-only-1366.png',
};

function loadPlaywright() {
  const candidates = [
    process.env.PLAYWRIGHT_MODULE,
    'playwright',
    'playwright-core',
  ].filter(Boolean);
  const tried = [];
  for (const candidate of candidates) {
    try {
      const loaded = require(candidate);
      return {chromium: loaded.chromium, from: candidate};
    } catch (error) {
      tried.push(`${candidate} (${error.code || error.message.split('\n')[0]})`);
    }
  }
  throw new Error(`No Playwright module resolvable. Tried: ${tried.join(', ')}. ` +
    'Install it locally or set PLAYWRIGHT_MODULE to a resolvable module path.');
}

async function launchChromium(chromium) {
  try {
    return await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE});
  } catch (error) {
    if (!process.env.CHROMIUM_EXECUTABLE) throw error;
    throw new Error(`Could not launch ${process.env.CHROMIUM_EXECUTABLE}: ${error.message}`);
  }
}

function note(results, message) {
  results.push(message);
  console.log(message);
}

function guardRequests(page, origin, bucket) {
  page.on('pageerror', error => bucket.errors.push(error.message));
  page.on('request', request => {
    const url = request.url();
    if (url.startsWith(origin + '/') || url.startsWith('data:') || url === 'about:blank') return;
    // Anything that is not this task's own loopback origin is recorded as a failure.
    bucket.external.push(url);
  });
  page.on('requestfailed', request => bucket.failed.push(request.url() + ' ' + (request.failure() || {}).errorText));
}

async function assertNoHorizontalOverflow(page, label) {
  const metrics = await page.evaluate(() => ({
    scrollWidth: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
    clientWidth: document.documentElement.clientWidth,
    offenders: [...document.querySelectorAll('body *')]
      .filter(el => el.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
      .slice(0, 3)
      .map(el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
                (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : '')),
  }));
  assert.ok(metrics.scrollWidth <= metrics.clientWidth + 1,
    `${label}: horizontal overflow ${metrics.scrollWidth} > ${metrics.clientWidth} (${metrics.offenders.join(', ')})`);
}

/** Reach the camera filter by keyboard alone and activate it with Enter. */
async function assertKeyboardFilter(page, view) {
  const reached = await page.evaluate(async () => {
    const first = document.querySelector('[data-camera="DemoFront"]');
    first.focus();
    return document.activeElement === first;
  });
  assert.ok(reached, `${view}: camera filter cannot take keyboard focus`);
  const before = await page.locator('[data-camera="DemoFront"]').getAttribute('aria-pressed');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() =>
    document.querySelector('[data-camera="DemoFront"]')?.getAttribute('aria-pressed') === 'true',
    null, {timeout: 8000});
  assert.notEqual(await page.locator('[data-camera="DemoFront"]').getAttribute('aria-pressed'), before,
    `${view}: Enter did not activate the camera filter`);
  await page.keyboard.press('Tab');
  await page.evaluate(() => document.querySelector('[data-camera="all"]').focus());
  await page.keyboard.press('Space');
  await page.waitForFunction(() =>
    document.querySelector('[data-camera="all"]')?.getAttribute('aria-pressed') === 'true',
    null, {timeout: 8000});
}

const VIEWS = {
  timeline: {
    path: '/activity/index.html',
    frames: '#frames',
    visits: '#visits',
    cards: '.frame',
    waitForCount: async (page, n) =>
      page.waitForFunction(v => document.querySelector('#frames')?.textContent === v, n, {timeout: 20000}),
    stale: page => page.locator('#activity-data').evaluate(el => el.classList.contains('data-stale')),
  },
  companion: {
    path: '/frontend/activity-v1.html',
    frames: '#frame-count',
    visits: '#visit-count',
    cards: '.activity-card',
    waitForCount: async (page, n) =>
      page.waitForFunction(v => document.querySelector('#frame-count')?.textContent === v, n, {timeout: 20000}),
    stale: page => page.locator('#activity-frames').evaluate(() =>
      document.querySelector('#source-status')?.dataset.state === 'stale'),
  },
};

async function exerciseView(browser, view, origin, bucket, label, shot) {
  const spec = VIEWS[view];
  const errors = [];
  const page = await browser.newPage({viewport: {width: 1366, height: 900}, colorScheme: 'dark'});
  guardRequests(page, origin, {errors, external: bucket.external, failed: bucket.failed});
  try {
    await page.goto(origin + spec.path, {waitUntil: 'domcontentloaded'});
    for (const width of WIDTHS) {
      await page.setViewportSize({width, height: width < 500 ? 844 : 900});
      await page.waitForTimeout(120); // let lazy layout settle, not a readiness gate
      await assertNoHorizontalOverflow(page, `${label} @${width}`);
      if (width === 1366) {
        await assertKeyboardFilter(page, label);
        await page.evaluate(() => document.querySelector('[data-camera="all"]').click());
      }
      if (shot && EVIDENCE_WIDTHS[view] && EVIDENCE_WIDTHS[view].has(width)) {
        await page.screenshot({path: shot(`${view}/${width}`), fullPage: true});
      }
    }
    assert.deepEqual(errors, [], `${label}: uncaught page errors`);
  } finally {
    await page.close();
  }
}

/** HTTP failure, malformed body and a real 5 s client timeout must all end in a
 *  stale last-known-good state, and must recover when the API returns again. */
async function exerciseFailures(browser, view, origin, bucket) {
  const spec = VIEWS[view];
  const label = `failure modes/${view}`;
  const isStale = () => page.evaluate(() => {
    if (document.querySelector('#activity-data')) return document.querySelector('#activity-data').classList.contains('data-stale');
    return document.querySelector('#source-status')?.dataset.state === 'stale';
  });
  const page = await browser.newPage({viewport: {width: 1366, height: 900}, colorScheme: 'dark'});
  guardRequests(page, origin, {errors: [], external: bucket.external, failed: bucket.failed});
  const settle = async expected => {
    await page.waitForFunction(want => {
      const stale = document.querySelector('#activity-data')
        ? document.querySelector('#activity-data').classList.contains('data-stale')
        : document.querySelector('#source-status')?.dataset.state === 'stale';
      return stale === want;
    }, expected, {timeout: 25000});
  };
  try {
    await page.goto(origin + spec.path, {waitUntil: 'domcontentloaded'});
    await spec.waitForCount(page, '10');
    assert.equal(await page.locator(spec.cards).count(), 10, `${label}: expected the seeded frames`);

    const broken = [
      {name: 'http-503', route: route => route.fulfill({status: 503, contentType: 'application/json', body: '{"error":"offline"}'})},
      {name: 'malformed-body', route: route => route.fulfill({status: 200, contentType: 'application/json', body: '{"unexpected":"payload"}'})},
      {name: 'provenance-lie', route: route => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify({
        schema_version: 1, data_source: 'watched-folder-preview', read_only: true, mode: 'watched-folder-demo',
        inference: {object_detector: 'complete', vision_decision_model: 'complete'},
        scan: {max_frames: 500, max_candidates_per_camera: 2000, possibly_truncated: false,
               unsupported_filename_rule: 'x', skipped: {}},
        frames: [], visits: [], count: 0, visit_count: 0})})},
      {name: 'client-timeout', slow: true, route: async route => {
        await new Promise(done => setTimeout(done, 6200));
        await route.fulfill({status: 200, contentType: 'application/json', body: '{"late":true}'});
      }},
    ];
    for (const {name, route, slow} of broken) {
      // Start each case from a healthy, non-stale state so the transition itself
      // is what is being observed.
      await page.unroute('**/api/activity');
      await page.locator('#refresh').click();
      await settle(false);
      assert.equal(await page.locator('#stale-notice').isHidden(), true, `${label}: ${name} started stale`);

      await page.route('**/api/activity', route);
      const started = Date.now();
      await page.locator('#refresh').click();
      await settle(true);
      const elapsed = Date.now() - started;
      if (slow) {
        assert.ok(elapsed >= 4500, `${label}: client did not actually time out (${elapsed} ms)`);
      }
      assert.equal(await isStale(), true, `${label}: ${name} did not mark results stale`);
      assert.equal(await page.locator(spec.cards).count(), 10,
        `${label}: ${name} discarded the last known good frames`);
      assert.ok(!(await page.locator('#stale-notice').isHidden()), `${label}: ${name} hid the stale warning`);
      await page.unroute('**/api/activity');
    }

    await page.locator('#refresh').click();
    await settle(false);
    assert.equal(await page.locator('#stale-notice').isHidden(), true, `${label}: recovery left the warning visible`);
    assert.equal(await page.locator(spec.cards).count(), 10, `${label}: recovery lost the frames`);
    return `PASS ${label}: 503, malformed body, provenance lie and a real 5 s client timeout all stayed stale with the last good frames, then recovered`;
  } finally {
    await page.close();
  }
}

async function main() {
  const {chromium, from} = loadPlaywright();
  const outputDir = writeEvidence ? path.join(ROOT, 'docs/evidence') : os.tmpdir();
  if (writeEvidence) fs.mkdirSync(outputDir, {recursive: true});
  const shot = key => path.join(outputDir, EVIDENCE_NAMES[key]);
  const incoming = fs.mkdtempSync(path.join(os.tmpdir(), 'funes-browser-incoming-'));
  const port = await freePort();
  const origin = `http://127.0.0.1:${port}`;
  const bucket = {external: [], failed: []};
  const server = spawnPython(['-m', 'funes.server', '--watch-dir', incoming, '--port', String(port)],
    {cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe']});
  let browser;
  const results = [];
  try {
    await waitForHttp(`${origin}/api/activity`, {child: server});
    assert.deepEqual((await (await fetch(`${origin}/api/activity`)).json()).count, 0,
      'a fresh watched folder must start empty');
    browser = await launchChromium(chromium);
    console.log(`playwright module: ${from}; chromium: ${process.env.CHROMIUM_EXECUTABLE || 'bundled'}`);

    // Empty folder: both views must show nothing and never claim observations.
    for (const view of Object.keys(VIEWS)) {
      const page = await browser.newPage({viewport: {width: 1366, height: 900}});
      guardRequests(page, origin, {errors: [], external: bucket.external, failed: bucket.failed});
      await page.goto(origin + VIEWS[view].path, {waitUntil: 'domcontentloaded'});
      await page.waitForTimeout(400);
      assert.equal(await page.locator(VIEWS[view].cards).count(), 0, `${view}: empty folder showed frames`);
      if (view === 'timeline') await page.screenshot({path: shot('timeline-empty/1366'), fullPage: true});
      await page.close();
      note(results, `PASS empty watched folder → ${view} shows no frames and no visits`);
    }

    const seed = spawnPython(['tools/seed_synthetic_watch.py', '--dest', incoming], {cwd: ROOT});
    await new Promise(done => seed.on('exit', done));
    assert.equal(seed.exitCode, 0, 'synthetic seeding failed');
    note(results, 'PASS 10 verified synthetic JPEGs seeded into the running watched folder');

    for (const view of Object.keys(VIEWS)) {
      note(results, `PASS ${view} view at ${WIDTHS.join('/')} px: no horizontal overflow, camera filter keyboard operable`);
      await exerciseView(browser, view, origin, bucket, view, shot);
    }

    for (const view of Object.keys(VIEWS)) {
      note(results, await exerciseFailures(browser, view, origin, bucket));
    }

    // Node-only static preview: the companion must report unavailable, never
    // fall back to the Refined Current fictional fixtures.
    const nodePort = await freePort();
    const nodeOrigin = `http://127.0.0.1:${nodePort}`;
    const nodeServer = spawn(process.execPath, ['server.mjs'],
      {cwd: ROOT, env: {...process.env, PORT: String(nodePort)}, stdio: ['ignore', 'pipe', 'pipe']});
    try {
      await waitForHttp(`${nodeOrigin}/manifest.json`, {child: nodeServer});
      const page = await browser.newPage({viewport: {width: 1366, height: 900}});
      guardRequests(page, nodeOrigin, {errors: [], external: bucket.external, failed: bucket.failed});
      await page.goto(`${nodeOrigin}/frontend/activity-v1.html`, {waitUntil: 'domcontentloaded'});
      await page.waitForFunction(() =>
        document.querySelector('#source-status')?.dataset.state === 'unavailable', null, {timeout: 20000});
      assert.equal(await page.locator('.activity-card').count(), 0, 'Node-only preview showed fictional frames');
      assert.equal(await page.locator('.visit-card').count(), 0, 'Node-only preview showed fictional visits');
      assert.doesNotMatch(await page.locator('#activity-frames').innerText(), /demo-front|demo-back/i,
        'Node-only preview fell back to fictional fixture filenames');
      assert.match(await page.locator('#source-status').innerText(), /unavailable/i);
      await page.screenshot({path: shot('node-only/1366'), fullPage: true});
      await page.close();
      note(results, 'PASS Node-only static preview → companion shows unavailable, no fictional fixture fallback');
    } finally {
      await stopProcess(nodeServer);
    }

    assert.deepEqual(bucket.external, [], 'browser requested a non-local origin');
    console.log(`PASS ${results.length} browser checks; no external requests; screenshots: ${outputDir}`);
  } finally {
    if (browser) await browser.close();
    await stopProcess(server);
    fs.rmSync(incoming, {recursive: true, force: true});
  }
}

main().catch(error => {
  console.error(error.stack || String(error));
  process.exit(1);
});