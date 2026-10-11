#!/usr/bin/env node
'use strict';
// #136: an active search/filter must not break the phone header (no overflow, Live badge
// visible, filter chip on one line, search field usable). Synthetic fixture stub only.
const { chromium } = require('playwright');
const URL = process.env.HEADER_SMOKE_URL || 'http://127.0.0.1:8899/';
if (!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(URL)) throw new Error('header smoke refuses non-fixture origin');
(async () => {
  const browser = await chromium.launch();
  const bad = [];
  try {
    for (const width of [320, 360, 390, 414]) {
      for (const theme of ['light', 'dark']) {
        const ctx = await browser.newContext({ viewport: { width, height: 700 }, hasTouch: true, isMobile: true });
        await ctx.addInitScript(t => localStorage.setItem('funes-vision.theme', t), theme);
        const page = await ctx.newPage();
        const errs = [];
        page.on('pageerror', e => errs.push(String(e)));
        await page.goto(URL, { waitUntil: 'domcontentloaded' });
        await page.waitForSelector('#date-list .fv-cal-month', { state: 'attached' });
        await page.locator('#search-trigger').click();
        await page.keyboard.type('zzzz');
        await page.keyboard.press('Escape');
        await page.waitForSelector('#filter-active-banner:not([hidden])', { state: 'visible' });
        const m = await page.evaluate(() => {
          const r = id => document.getElementById(id).getBoundingClientRect();
          return { over: document.documentElement.scrollWidth - document.documentElement.clientWidth,
            liveRight: Math.round(r('live-toggle').right), searchW: Math.round(r('search-trigger').width),
            bannerH: Math.round(r('filter-active-banner').height), vw: document.documentElement.clientWidth };
        });
        const tag = `${width}/${theme}`;
        if (m.over > 0) bad.push(`${tag}: page is ${m.over}px wider than the viewport`);
        if (m.liveRight > m.vw) bad.push(`${tag}: Live badge right edge ${m.liveRight}px is past the viewport (${m.vw}px)`);
        if (m.searchW < 200) bad.push(`${tag}: search field collapsed to ${m.searchW}px`);
        if (m.bannerH > 40) bad.push(`${tag}: the filter chip wraps (${m.bannerH}px high)`);
        if (errs.length) bad.push(`${tag}: page errors ${errs.join(' | ')}`);
        await ctx.close();
      }
    }
  } finally { await browser.close(); }
  if (bad.length) { console.error('FAIL\n  ' + bad.join('\n  ')); process.exitCode = 1; }
  else console.log('header-search smoke: no overflow, Live visible, chip on one line, search >= 200px at 320/360/390/414 (light+dark)');
})().catch(e => { console.error(e); process.exitCode = 1; });
