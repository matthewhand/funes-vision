#!/usr/bin/env node
// Browser proof for UI-only appearance modes. Uses synthetic fixtures ONLY.
'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const ORIGIN = process.env.THEME_SMOKE_URL || 'http://127.0.0.1:8899/';
const OUT = path.resolve(process.env.THEME_SHOTS_DIR || 'theme-previews');
async function run() {
  if (!/^http:\/\/127\.0\.0\.1:\d+\//.test(ORIGIN)) {
    throw new Error('Refusing remote or live camera origin: ' + ORIGIN);
  }
  const browser = await chromium.launch();
  fs.mkdirSync(OUT, { recursive: true });
  try {
    for (const [name, width, height] of [
      ['mobile', 390, 844], ['laptop', 1366, 900], ['desktop', 1920, 1080],
    ]) {
      const ctx = await browser.newContext({
        viewport: { width, height }, colorScheme: 'light', reducedMotion: 'reduce',
      });
      const page = await ctx.newPage();
      await page.goto(ORIGIN, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector('#fv-theme-select');
      const theme = () => page.locator('html').getAttribute('data-theme');
      await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
      assert.strictEqual(await theme(), 'light', 'system light (' + name + ')');
      await page.locator('#fv-theme-select').selectOption('dark');
      assert.strictEqual(await theme(), 'dark', 'dark selection (' + name + ')');
      await page.screenshot({ path: path.join(OUT, name + '-dark.png'), animations: 'disabled' });
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForSelector('#fv-theme-select');
      assert.strictEqual(await theme(), 'dark', 'dark persists after reload (' + name + ')');
      await page.locator('#fv-theme-select').selectOption('light');
      assert.strictEqual(await theme(), 'light', 'light selection (' + name + ')');
      await page.screenshot({ path: path.join(OUT, name + '-light.png'), animations: 'disabled' });
      await page.locator('#fv-theme-select').selectOption('system');
      await page.emulateMedia({ colorScheme: 'dark' });
      await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
      await page.emulateMedia({ colorScheme: 'light' });
      await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
      const sizes = await page.evaluate(() => ({
        width: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
      }));
      assert(sizes.scroll <= sizes.width + 1,
        name + ' horizontal overflow: ' + JSON.stringify(sizes));
      console.log(name + ': dark/light/system, persistence, responsive width OK');
      await ctx.close();
    }
  } finally {
    await browser.close();
  }
}
run().catch(err => { console.error(err); process.exitCode = 1; });
