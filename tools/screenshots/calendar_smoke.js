#!/usr/bin/env node
// Browser proof of responsive archive calendar with SYNTHETIC fixture data only.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const ORIGIN = process.env.CALENDAR_SMOKE_URL || 'http://127.0.0.1:8899/';
if (!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(ORIGIN)) {
  throw new Error('Calendar snapshots require local fixture proxy only');
}
const OUT = path.resolve(process.env.CALENDAR_SHOTS_DIR || 'calendar-previews');
async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  try {
    for (const [device, w, h] of [['mobile', 390, 844], ['desktop', 1440, 900]]) {
      for (const theme of ['light', 'dark']) {
        const context = await browser.newContext({
          viewport: { width: w, height: h }, reducedMotion: 'reduce',
        });
        await context.addInitScript(value => {
          localStorage.setItem('funes-vision.theme', value);
        }, theme);
        const page = await context.newPage();
        await page.goto(ORIGIN, { waitUntil: 'domcontentloaded' });
        await page.waitForSelector('#date-list .fv-cal-grid', { state: 'attached' });
        if (!await page.locator('#date-list').isVisible()) {
          await page.locator('#sidebar-toggle').click();
        }
        await page.locator('#date-list .fv-cal-grid').waitFor({ state: 'visible' });
        assert.strictEqual(await page.locator('#date-list .fv-cal-day').count(), 30,
          'fixture archive must show June 2026 without a lengthy day list');
        assert.strictEqual(await page.locator('#date-list .fv-cal-month').inputValue(), '2026-06');
        const june18 = page.locator('#date-list .fv-cal-day').filter({
          hasText: /^18$/,
        });
        assert.strictEqual(await june18.getAttribute('aria-pressed'), 'true',
          'latest recorded date should be selected');
        assert.strictEqual(await page.locator('#date-list .fv-cal-day.has-recordings').count(), 1);
        await page.locator('#date-list .fv-cal-all').click();
        assert.strictEqual(await page.locator('#date-list .fv-cal-all').getAttribute('aria-pressed'), 'true',
          'All dates should clear day selection');
        await page.locator('#date-list .fv-cal-day.has-recordings').click();
        assert.strictEqual(await page.locator('#date-list .fv-cal-all').getAttribute('aria-pressed'), 'false',
          'choosing a recorded day restores date filter');
        assert.strictEqual(await page.locator('html').getAttribute('data-theme'), theme);
        const widths = await page.evaluate(() => ({
          document: document.documentElement.scrollWidth,
          viewport: document.documentElement.clientWidth,
        }));
        assert(widths.document <= widths.viewport + 1,
          'calendar caused horizontal overflow: ' + JSON.stringify(widths));
        await page.screenshot({
          path: path.join(OUT, device + '-' + theme + '.png'),
          animations: 'disabled', timeout: 25000,
        });
        console.log(device, theme, 'calendar OK');
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
