#!/usr/bin/env node
'use strict';
// #135: the chronological event feed must be on the first screen on short/small viewports.
// Synthetic fixture stub only (tools/screenshots/proxy.py, default :8899).
const { chromium } = require('playwright');
const URL = process.env.FIRST_SCREEN_URL || 'http://127.0.0.1:8899/';
if (!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(URL)) throw new Error('first-screen smoke refuses non-fixture origin');
const VIEWPORTS = [[844, 390, true], [667, 375, true], [320, 640, true], [390, 844, true], [1024, 600, false], [1024, 768, false], [1366, 768, false], [1440, 900, false]];
const MIN_VISIBLE_FEED_PX = 120;   // feed pixels that must be on the first screen
const MIN_FEED_BOX_PX = 200;       // the scroller itself must not collapse (was 74px at 1024x600)
(async () => {
  const browser = await chromium.launch();
  const bad = [];
  try {
    for (const [w, h, touch] of VIEWPORTS) {
      for (const theme of ['light', 'dark']) {
        const ctx = await browser.newContext({ viewport: { width: w, height: h }, hasTouch: touch, isMobile: touch });
        await ctx.addInitScript(t => localStorage.setItem('funes-vision.theme', t), theme);
        const page = await ctx.newPage();
        await page.goto(URL, { waitUntil: 'domcontentloaded' });
        await page.waitForSelector('#date-list .fv-cal-month', { state: 'attached' });
        await page.waitForSelector('#image-grid .visit-row', { state: 'attached' });
        await page.waitForTimeout(500);
        const m = await page.evaluate(() => {
          const g = document.getElementById('image-grid').getBoundingClientRect();
          const row = document.querySelector('#image-grid .visit-row').getBoundingClientRect();
          return { top: Math.round(g.top), height: Math.round(g.height), rowTop: Math.round(row.top), vh: innerHeight };
        });
        const visible = Math.min(m.vh, m.top + m.height) - m.top;
        const tag = `${w}x${h}/${theme}`;
        if (visible < MIN_VISIBLE_FEED_PX) bad.push(`${tag}: only ${visible}px of the feed is on the first screen (feed top ${m.top}, viewport ${m.vh})`);
        if (m.height < MIN_FEED_BOX_PX) bad.push(`${tag}: the feed box collapsed to ${m.height}px`);
        if (m.rowTop >= m.vh - 40) bad.push(`${tag}: the first event row starts at ${m.rowTop}px, below the fold (${m.vh}px)`);
        await ctx.close();
      }
    }
  } finally { await browser.close(); }
  if (bad.length) { console.error('FAIL\n  ' + bad.join('\n  ')); process.exitCode = 1; }
  else console.log('first-screen smoke: event feed visible on the first screen at ' + VIEWPORTS.map(v => v[0] + 'x' + v[1]).join(', ') + ' (light+dark)');
})().catch(e => { console.error(e); process.exitCode = 1; });
