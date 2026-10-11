#!/usr/bin/env node
'use strict';
// Issue #120: the SPA must stay usable with inference STOPPED (Ollama down).
// Every published shot and every other smoke run is taken against
// fixtures/api, where /api/health says ok and llm.reachable is true — the one
// state nobody ships. This drives the offline fixture instead:
//
//   SCREENSHOT_API_DIR=tools/screenshots/fixtures/api-offline \
//   SCREENSHOT_HEALTH_STATUS=503 python3 tools/screenshots/proxy.py &
//   SCREENSHOT_OUT=layout-previews/offline \
//     node tools/screenshots/offline_inference_smoke.js
//
// The proxy only ever serves synthetic fixtures; it never contacts a live
// api_server or Ollama, and refuses /mnt/models roots at startup.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const URL = process.env.OFFLINE_SMOKE_URL || 'http://127.0.0.1:8898/';
if (!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(URL)) throw new Error('Refuse non-fixture URL');
const OUT = path.resolve(process.env.SCREENSHOT_OUT || 'offline-previews');
// The only console error the offline fixture is allowed to produce: the 503
// /api/health it is *meant* to answer. Matched by URL, so anything else (a
// failed request, an uncaught exception) is a hard failure.
const HEALTH_PATH = '/api/health';
// "Still loading after 8s" is the bug class this catches; a placeholder that
// clears sooner returns immediately.
const STUCK_MS = 8000;
const PASS_GLYPH = '\u2713';  // &#10003; — healthy row
const FAIL_GLYPH = '\u2717';  // &#10007; — degraded row
const VIEWS = [['mobile', 390, 844, true], ['desktop', 1440, 900, false]];
const THEMES = ['light', 'dark'];
// label is for messages only; the Motion tab's data-filter really is "all".
const TABS = [['events', 'Timeline'], ['objects', 'Objects'], ['all', 'Motion']];

// The pipeline panel's text is only legible as "degraded" through symbols, so
// findings are reported rather than asserted.

async function stuckLoading(page) {
  return page.evaluate((healthPath) => {
    const bad = [];
    for (const el of document.querySelectorAll('body *')) {
      const text = (el.textContent || '').trim();
      if (!/loading/i.test(text) || text.length > 80) continue;  // a placeholder, not a container
      // The calendar's own "Loading calendar…" stub is documented: it is
      // replaced by the grid, so once #date-list has one it is legitimate.
      if (el.closest('#date-list') && document.querySelector('#date-list .fv-cal-grid')) continue;
      const box = el.getBoundingClientRect();
      if (!box.width && !box.height) continue;  // hidden, not stuck
      bad.push((el.id ? '#' + el.id : el.tagName.toLowerCase()) + ' "' + text.slice(0, 40) + '"');
    }
    return bad;
  }, HEALTH_PATH);
}

async function waitNotStuckLoading(page, tag) {
  const deadline = Date.now() + STUCK_MS;
  let stuck = await stuckLoading(page);
  while (stuck.length && Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 250));
    stuck = await stuckLoading(page);
  }
  assert.deepEqual(stuck, [], tag + ' still showing Loading after ' + STUCK_MS + 'ms: ' + stuck.join(', '));
}

function watchErrors(page) {
  const problems = [];
  let healthErrors = 0;
  page.on('pageerror', err => problems.push('pageerror: ' + ((err && err.message) || err)));
  page.on('console', msg => {
    if (msg.type() !== 'error') return;
    const where = (msg.location() || {}).url || '';
    if (where.includes(HEALTH_PATH)) { healthErrors++; return; }
    problems.push('console error: ' + msg.text() + (where ? ' @ ' + where : ''));
  });
  return { problems, tolerated: () => healthErrors };
}

async function checkHealth(page, tag) {
  const res = await page.evaluate(async (p) => {
    const r = await fetch(p, { headers: { accept: 'application/json' } });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, HEALTH_PATH);
  assert.strictEqual(res.status, 503,
    tag + ' the offline fixture must answer ' + HEALTH_PATH + ' with 503 (got ' + res.status + ')');
  // a11y_audit.js refuses an origin whose health does not report the fixture.
  assert.ok(res.body && res.body.fixture === true,
    tag + ' degraded health must still report the fixture stub: ' + JSON.stringify(res.body));
  assert.strictEqual(res.body.status, 'degraded');
  assert.strictEqual(res.body.checks.llm_reachable, false,
    tag + ' degraded health must say the LLM is unreachable');
  assert.deepEqual(Object.keys(res.body.checks).sort(),
    ['disk_space', 'inotify', 'llm_reachable', 'recent_sweep'],
    tag + ' health checks must mirror api_server.py health_summary()');
}

async function checkTabs(page, tag) {
  for (const [filter, label] of TABS) {
    await page.locator('#filter-tabs [data-filter="' + filter + '"]').evaluate(el => el.click());
    const view = await page.evaluate(() => {
      const grid = document.getElementById('image-grid');
      return {
        cards: grid.querySelectorAll('.image-card').length,
        visits: grid.querySelectorAll('.visit-log, .visit-card, .visit-row').length,
        empty: grid.querySelectorAll('.empty-state').length,
        text: (grid.textContent || '').replace(/\s+/g, ' ').trim(),
      };
    });
    assert.ok(view.cards + view.visits + view.empty > 0,
      tag + ' ' + label + ' tab must render frames, the visit timeline or its documented empty state');
    assert.ok(!/^Loading/.test(view.text), tag + ' ' + label + ' tab is stuck on a placeholder');
  }
}

async function checkThumbnails(page, tag) {
  // Ends on the Motion tab, so snapshot cards are the grid's content.
  await page.waitForFunction(() => {
    const img = document.querySelector('#image-grid .image-card img');
    return !!img && img.naturalWidth > 0;
  }, null, { timeout: 20000 });
  const grid = await page.evaluate(() => ({
    cards: document.querySelectorAll('#image-grid .image-card').length,
    decoded: [...document.querySelectorAll('#image-grid .image-card img')]
      .filter(img => img.naturalWidth > 0).length,
  }));
  assert.ok(grid.cards > 0, tag + ' the grid must render snapshot cards with inference offline');
  assert.ok(grid.decoded > 0,
    tag + ' at least one thumbnail must decode (naturalWidth > 0), got ' + grid.decoded);
}

async function checkCalendar(page, tag, width) {
  const toggle = page.locator('#sidebar-toggle');
  if (width <= 992 && await toggle.getAttribute('aria-expanded') === 'false') {
    await toggle.evaluate(el => el.click());
  }
  const day = page.locator('#date-list .fv-cal-day.has-recordings').first();
  await day.waitFor({ state: 'visible', timeout: 15000 });
  await day.evaluate(el => el.click());
  assert.strictEqual(await day.getAttribute('aria-pressed'), 'true',
    tag + ' picking a calendar day must select it');
  assert.strictEqual(await page.locator('#date-list .fv-cal-all').getAttribute('aria-pressed'), 'false',
    tag + ' a day selection must clear the All-dates filter');
  await page.waitForFunction(
    () => document.querySelectorAll('#image-grid .image-card').length > 0,
    null, { timeout: 15000 });
  await page.screenshot({ path: path.join(OUT, tag + '-calendar.png'), animations: 'disabled' });
}

async function checkSystemPanel(page, tag, findings) {
  await page.locator('#btn-system-status').evaluate(el => el.click());
  await page.locator('#system-dropdown').waitFor({ state: 'visible', timeout: 10000 });
  await page.waitForFunction(
    () => (document.getElementById('system-status-content').textContent || '').includes('LLM:'),
    null, { timeout: 15000 });
  const panel = await page.locator('#system-status-content').evaluate(el => {
    const row = [...el.querySelectorAll('div')].find(d => /^LLM:/.test(d.textContent.trim()));
    const trail = el.querySelector('[data-audit-trail]');
    return {
      text: (el.textContent || '').replace(/\s+/g, ' ').trim(),
      llm: row ? row.textContent.replace(/\s+/g, ' ').trim() : null,
      trailRows: trail ? trail.querySelectorAll(':scope > div').length : 0,
      trailText: trail ? (trail.textContent || '').replace(/\s+/g, ' ').trim() : '',
    };
  });
  // The panel DOES render LLM/inference state, so assert what it prints for
  // llm.reachable=false (index.html:9164 — `LLM: <model> <ok|fail>`).
  assert.ok(panel.llm, tag + ' the pipeline panel must print an LLM row while the backend is down');
  assert.ok(panel.llm.startsWith('LLM: '), tag + ' unexpected LLM row: ' + panel.llm);
  assert.ok(panel.llm.includes(FAIL_GLYPH),
    tag + ' an unreachable LLM must be marked failed, got: ' + panel.llm);
  assert.ok(!panel.llm.includes(PASS_GLYPH),
    tag + ' an unreachable LLM must not look healthy: ' + panel.llm);
  assert.match(panel.text, /Inference: unavailable/,
    tag + ' the panel must say inference is unavailable (not healthy idle) when the backend is down');
  // Queue: the backlog only grows while Ollama refuses connections.
  assert.match(panel.text, /4 new · 6 priority · 6 backfill · 0 verified \(10 on disk\)/,
    tag + ' the queue must report the offline fixture backlog: ' + panel.text.slice(0, 400));
  assert.match(panel.text, /16 pending/, tag + ' the backlog must total the offline pending work');
  assert.match(panel.text, /0 verified, 16 pending/, tag + ' deep analysis must show zero verified');
  assert.ok(!panel.text.includes('\u2248'),
    tag + ' a null deep_eta_s must not print a duration estimate');
  // The audit trail must still render the failed runs.
  assert.strictEqual(panel.trailRows, 3, tag + ' the audit trail must list every logged run');
  assert.match(panel.trailText, /failed/,
    tag + ' a failed inference must be marked failed in the audit trail');
  // --- findings: observed, reported, never fatal -------------------------
  findings.push(tag + ': the only signal that Ollama is unreachable is the "' + FAIL_GLYPH +
    '" glyph in "' + panel.llm + '" — no wording (no "unreachable"/"offline"), so the state is ' +
    'symbol- and colour-only and is not announced to a screen reader.');
  findings.push(tag + ': the audit trail prints the failed runs but never surfaces the ' +
    'inference_log "error" reason ("connection refused"), so the operator cannot see why.');
  const okPct = (panel.text.match(/(\d+)% ok/) || [])[1];
  if (okPct !== undefined && Number(okPct) > 0) {
    findings.push(tag + ': the panel still reports "…% ok" from metrics while llm.reachable is ' +
      'false and all 3 logged runs failed, so the Inference section can look healthy: "' +
      panel.text.slice(panel.text.indexOf('Inference (last')) .slice(0, 90) + '"');
  }
  await page.screenshot({ path: path.join(OUT, tag + '-system-status.png'), animations: 'disabled' });
  await page.keyboard.press('Escape');
  return panel.text;
}

// The light/dark passes seed localStorage like calendar_smoke.js does; this is
// the real user path: change the theme through the UI and reload. Deliberately
// a context WITHOUT addInitScript, which would rewrite the stored theme on
// every navigation and hide a failure to persist it.
async function checkThemePersists(browser, size, width, height) {
  const ctx = await browser.newContext({ viewport: { width, height }, reducedMotion: 'reduce' });
  const page = await ctx.newPage();
  try {
    await page.goto(URL, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => localStorage.setItem('funes-vision.theme', 'light'));
    await page.reload({ waitUntil: 'domcontentloaded' });
    const theme = () => page.locator('html').getAttribute('data-theme');
    assert.strictEqual(await theme(), 'light', size + ' must start in the stored light theme');
    await page.locator('#btn-manage-blacklist').click();
    await page.locator('#fv-theme-select').selectOption('dark');
    assert.strictEqual(await theme(), 'dark', size + ' the theme control must work with the API degraded');
    await page.keyboard.press('Escape');
    await page.reload({ waitUntil: 'domcontentloaded' });
    assert.strictEqual(await theme(), 'dark',
      size + ' a theme change must survive a reload with inference offline');
    assert.strictEqual(await page.evaluate(() => localStorage.getItem('funes-vision.theme')), 'dark',
      size + ' the chosen theme must be persisted in localStorage');
    await page.screenshot({
      path: path.join(OUT, 'dark-' + width + 'x' + height + '-persist.png'),
      animations: 'disabled',
    });
  } finally {
    await ctx.close();
  }
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const findings = [];
  const browser = await chromium.launch();
  let combos = 0;
  try {
    for (const [size, width, height, hasTouch] of VIEWS) {
      for (const theme of THEMES) {
        const tag = theme + '-' + width + 'x' + height;
        const ctx = await browser.newContext({
          viewport: { width, height }, reducedMotion: 'reduce', hasTouch,
        });
        await ctx.addInitScript(value => localStorage.setItem('funes-vision.theme', value), theme);
        const page = await ctx.newPage();
        const { problems, tolerated } = watchErrors(page);
        try {
          await page.goto(URL, { waitUntil: 'domcontentloaded' });
          await page.waitForSelector('#date-list .fv-cal-grid', { state: 'attached', timeout: 20000 });
          assert.strictEqual(await page.locator('html').getAttribute('data-theme'), theme,
            tag + ' must honour the stored theme');
          await checkHealth(page, tag);
          await checkTabs(page, tag);
          await checkThumbnails(page, tag);
          await checkCalendar(page, tag, width);
          await checkSystemPanel(page, tag, findings);
          await waitNotStuckLoading(page, tag);
          // Asserted last so the captured screenshot survives a failure.
          assert.deepEqual(problems, [], tag + ' logged errors:\n  ' + problems.join('\n  '));
          await page.screenshot({ path: path.join(OUT, tag + '-gallery.png'), animations: 'disabled' });
          console.log(size, theme, 'offline inference OK (health 503s tolerated: ' + tolerated() + ')');
          combos++;
        } finally {
          await ctx.close();
        }
      }
      await checkThemePersists(browser, size, width, height);
    }
  } finally {
    await browser.close();
  }
  for (const line of findings) console.log('FINDING: ' + line);
  console.log('offline-inference-smoke: ' + combos + ' viewport/theme combination(s) OK, shots in ' + OUT);
}

main().catch(err => { console.error(err); process.exitCode = 1; });
