#!/usr/bin/env node
'use strict';
// Regressions #114, #117, #121, #122 with fabricated camera snapshots.
// Runs ONLY on the fixture server, never on a real camera or Ollama.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const URL = process.env.LAYOUT_SMOKE_URL || 'http://127.0.0.1:8899/';
if (!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(URL)) throw new Error('Refuse non-fixture URL');
const OUT = path.resolve('layout-previews');
async function main() {
  fs.mkdirSync(OUT, { recursive:true });
  const browser = await chromium.launch();
  try {
    for (const [size, width, height] of [
      ['small',320,568], ['mobile',390,844], ['landscape',844,390],
      ['tablet',768,1024], ['desktop',1440,900],
    ]) {
      const ctx = await browser.newContext({ viewport:{ width,height }, reducedMotion:'reduce' });
      await ctx.addInitScript(() => localStorage.setItem('funes-vision.theme','light'));
      const page = await ctx.newPage();
      await page.goto(URL, { waitUntil:'domcontentloaded' });
      await page.waitForSelector('#date-list .fv-cal-grid', { state:'attached' });
      await page.locator('#filter-tabs [data-filter="events"]').evaluate(el=>el.click());
      const list = page.locator('#image-grid .visit-log');
      await list.waitFor({ state:'attached', timeout:15000 });
      const timeline = await page.locator('#image-grid').evaluate(grid => ({
        first: grid.firstElementChild?.className,
        last: grid.lastElementChild?.id,
        gap: grid.querySelector('.visit-log').getBoundingClientRect().top - grid.getBoundingClientRect().top
      }));
      assert.match(String(timeline.first), /visit-log/, size+' Timeline must begin with visit list');
      assert.equal(timeline.last, 'scroll-sentinel', size+' scroll sentinel must be final grid child');
      assert(timeline.gap < 24, size+' timeline top gap '+timeline.gap);

      const calendar = page.locator('#sidebar-toggle');
      if (width <= 768) {
        assert(await calendar.locator('.sidebar-toggle-label').isVisible(),
          size+' mobile Calendar label must be discoverable');
      }
      if (width <= 992) {
        let collapsed = await calendar.getAttribute('aria-expanded');
        if (collapsed === 'false') await calendar.evaluate(el => el.click());
        assert.equal(await calendar.getAttribute('aria-expanded'),'true');
        const aside = await page.locator('.workspace aside').boundingBox();
        assert(aside.width >= width-2, size+' calendar sheet must occupy viewport width');
        const sheetProps = await page.locator('.workspace aside').evaluate(el => ({
          position: getComputedStyle(el).position,
          bottom: getComputedStyle(el).bottom,
          height: window.innerHeight,
          top: el.getBoundingClientRect().top,
          bottomEdge: el.getBoundingClientRect().bottom,
          rootClass: el.parentElement.className,
        }));
        assert(aside.y + aside.height >= height-2,
          size+' sheet must be anchored at viewport bottom: '+JSON.stringify({aside,sheetProps}));
        assert(await page.locator('#fv-calendar-close').isVisible(), size+' visible dismiss button');
        // Modal must not push the gallery down the document. Verify keyboard
        // close and focus restoration before interacting with the real feed.
        await page.keyboard.press('Escape');
        assert.equal(await calendar.getAttribute('aria-expanded'),'false');
        assert(await calendar.evaluate(el => document.activeElement === el), size+' Escape restores opener focus');
        assert(!await page.locator('#fv-calendar-close').isVisible(), size+' sheet dismissed');
      }
      await page.locator('#filter-tabs [data-filter="all"]').evaluate(el=>el.click());
      await page.locator('#image-grid .image-card').first().waitFor({state:'attached',timeout:15000});
      const gridSize = await page.locator('#image-grid').evaluate(grid => grid.clientHeight);
      if (width <= 992) assert(gridSize >= Math.min(0.6*height, 320)-1,
        size+' gallery must remain navigable with calendar open (height='+gridSize+')');
      if (width <= 768) {
        // Settings lives in the header; synthetically activate without scrolling
        // the gallery past the menu trigger. The rectangle test is real layout.
        await page.locator('#btn-manage-blacklist').evaluate(el=>el.click());
        const sheet = page.locator('#blacklist-dropdown');
        await sheet.waitFor({state:'visible'});
        const bounds = await sheet.boundingBox();
        assert(bounds.height >= height*0.8,
          size+' settings should be viewport-height (got '+bounds.height+' of '+height+')');
        await page.keyboard.press('Escape');
      }
      await page.screenshot({path:path.join(OUT,size+'-light.png'),animations:'disabled'});
      // A nonzero scroll region alone is not enough: the gallery must be
      // reachable on short screens after dismissing the calendar sheet.
      const firstCard = page.locator('#image-grid .image-card').first();
      await firstCard.scrollIntoViewIfNeeded({timeout:7000});
      const reachable = await firstCard.evaluate(card => {
        const b = card.getBoundingClientRect();
        const x = Math.min(innerWidth-1, Math.max(1,b.left + b.width/2));
        const y = Math.min(innerHeight-1, Math.max(1,b.top + Math.min(b.height/2,80)));
        return { x,y,bottom:b.bottom,top:b.top,
          hit: document.elementFromPoint(x,y)?.closest('.image-card') === card };
      });
      assert(reachable.hit, size+' gallery card must be hit-testable after scrolling: '+JSON.stringify(reachable));
      // Do not mistake a clickable black placeholder for a usable camera
      // view: lazy-loaded fixture thumbnails must also finish decoding.
      await page.waitForFunction(() => {
        const img = document.querySelector('#image-grid .image-card img.card-img');
        return !!img && img.classList.contains('loaded') && img.naturalWidth > 0;
      }, null, { timeout: 10000 });
      await page.screenshot({path:path.join(OUT,size+'-gallery-light.png'),animations:'disabled'});
      console.log(size, 'timeline/layout/settings OK, grid=',gridSize);
      await ctx.close();
    }
  } finally { await browser.close(); }
}
main().catch(err=>{console.error(err);process.exitCode=1;});
