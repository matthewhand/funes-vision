#!/usr/bin/env node
'use strict';
// #115 / #119: the phone calendar is a modal bottom-sheet drawer, it never
// moves the gallery, every control is a 44x44 target, Escape / backdrop /
// Close button dismiss it and give focus back to #sidebar-toggle, Tab stays
// inside it, and keyboard focus survives the calendar's re-render.
// Fixture server only (synthetic, fictional snapshots) - never a live camera.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const URL = process.env.CALENDAR_SMOKE_URL || process.env.LAYOUT_SMOKE_URL || 'http://127.0.0.1:8899/';
if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+\/$/.test(URL)) throw new Error('Refuse non-fixture URL');
const OUT = path.resolve(process.env.SCREENSHOT_OUT || 'mobile-calendar-previews');
// Multi-month archive so month navigation / re-render focus can be exercised.
const NAMES = ['20251231101500000', '20260102090000000', '20260618101500000', '20260618170000000']
  .map(t => `10.0.0.21_01_${t}_MOTDEC.jpg`);
const VIEWPORTS = [[320, 640], [360, 740], [390, 844], [414, 896], [768, 1024]];

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const failures = [];
  const check = (cond, msg) => { if (!cond) failures.push(msg); };
  try {
    for (const theme of ['light', 'dark']) for (const [w, h] of VIEWPORTS) {
      const tag = `${theme}-${w}x${h}`;
      const ctx = await browser.newContext({ viewport: { width: w, height: h }, hasTouch: true, reducedMotion: 'reduce' });
      await ctx.addInitScript(t => localStorage.setItem('funes-vision.theme', t), theme);
      const page = await ctx.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(String(e)));
      await page.route('**/images.json', r => r.fulfill({ json: NAMES }));
      await page.route('**/analysis.json', r => r.fulfill({ json: {} }));
      await page.goto(URL, { waitUntil: 'domcontentloaded' });
      await page.waitForSelector('#date-list .fv-cal-grid', { state: 'attached' });
      await page.waitForTimeout(500);
      const toggle = page.locator('#sidebar-toggle');
      const aside = page.locator('#archive-sidebar');
      const state = () => page.evaluate(() => {
        const a = document.getElementById('archive-sidebar'), t = document.getElementById('sidebar-toggle');
        const r = a.getBoundingClientRect(), g = document.getElementById('image-grid').getBoundingClientRect();
        const ae = document.activeElement;
        return {
          expanded: t.getAttribute('aria-expanded'), pressed: t.hasAttribute('aria-pressed'),
          role: a.getAttribute('role'), modal: a.getAttribute('aria-modal'),
          visible: getComputedStyle(a).display !== 'none', inView: r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth,
          gridTop: Math.round(g.top), inside: a.contains(ae), onToggle: ae === t, focusKey: ae && ae.dataset ? ae.dataset.calFocus || '' : '',
          overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          scrimShown: !document.getElementById('sidebar-scrim').hidden,
        };
      });

      const s0 = await state();
      check(s0.expanded === 'false' && !s0.pressed, `${tag}: toggle starts aria-expanded=false with no aria-pressed (${JSON.stringify(s0)})`);
      check(await toggle.locator('.sidebar-toggle-label').isVisible(), `${tag}: Calendar label visible`);
      const gridBefore = s0.gridTop;

      // --- open ---
      await toggle.tap();
      await page.waitForTimeout(350);
      const s1 = await state();
      check(s1.expanded === 'true' && s1.role === 'dialog' && s1.modal === 'true', `${tag}: open drawer is a modal dialog`);
      check(s1.inView, `${tag}: drawer fits inside the viewport`);
      check(Math.abs(s1.gridTop - gridBefore) <= 1, `${tag}: opening moved the gallery ${gridBefore} -> ${s1.gridTop}`);
      check(s1.inside, `${tag}: focus moved into the drawer`);
      check(s1.scrimShown, `${tag}: scrim shown`);
      check(s1.overflow <= 0, `${tag}: horizontal overflow ${s1.overflow}px`);
      const small = await page.evaluate(() => [...document.querySelectorAll(
        '#archive-sidebar .fv-cal-arrow, #archive-sidebar .fv-cal-month, #archive-sidebar .fv-cal-all, #archive-sidebar .fv-cal-close, #archive-sidebar .fv-cal-day:not(:disabled)')]
        .map(el => { const r = el.getBoundingClientRect(); return { c: el.className, w: Math.round(r.width), h: Math.round(r.height) }; })
        .filter(x => x.w < 44 || x.h < 44));
      check(small.length === 0, `${tag}: targets under 44px: ${JSON.stringify(small.slice(0, 3))}`);
      await page.screenshot({ path: path.join(OUT, `${tag}-open.png`) });

      // --- Tab stays inside (forward and back) ---
      let escaped = false;
      for (let i = 0; i < 14; i++) { await page.keyboard.press('Tab'); if (!(await state()).inside) escaped = true; }
      for (let i = 0; i < 4; i++) { await page.keyboard.press('Shift+Tab'); if (!(await state()).inside) escaped = true; }
      check(!escaped, `${tag}: Tab / Shift+Tab left the open drawer`);

      // --- #119 focus retention across re-render ---
      await page.locator('#archive-sidebar [data-cal-focus="prev"]').focus();
      const wasPrevDisabled = await page.locator('#archive-sidebar [data-cal-focus="prev"]').isDisabled();
      if (!wasPrevDisabled) {
        await page.keyboard.press('Enter');
        await page.waitForTimeout(150);
        const k = (await state()).focusKey;
        check(k === 'prev' || k === 'month', `${tag}: focus lost after Previous month (focusKey='${k}')`);
      }
      await page.locator('#archive-sidebar [data-cal-focus="month"]').focus();
      await page.locator('#archive-sidebar [data-cal-focus="month"]').selectOption({ index: 0 });
      await page.waitForTimeout(150);
      let st = await state();
      check(st.inside && st.focusKey === 'month', `${tag}: focus lost after month select (focusKey='${st.focusKey}', inside=${st.inside})`);
      const nextBtn = page.locator('#archive-sidebar [data-cal-focus="next"]');
      if (!(await nextBtn.isDisabled())) {
        await nextBtn.focus(); await page.keyboard.press('Enter'); await page.waitForTimeout(150);
        st = await state();
        check(st.inside && (st.focusKey === 'next' || st.focusKey === 'month'), `${tag}: focus lost after Next month (focusKey='${st.focusKey}')`);
      }

      // --- Escape closes + focus returns to toggle ---
      await page.keyboard.press('Escape');
      await page.waitForTimeout(250);
      st = await state();
      check(st.expanded === 'false' && !st.role && st.onToggle && !st.scrimShown, `${tag}: Escape must close, drop dialog role and return focus to the toggle (${JSON.stringify(st)})`);
      check(Math.abs(st.gridTop - gridBefore) <= 1, `${tag}: gallery position changed after close`);

      // --- backdrop click closes ---
      await toggle.tap(); await page.waitForTimeout(250);
      await page.mouse.click(Math.floor(w / 2), 4);
      await page.waitForTimeout(250);
      st = await state();
      check(st.expanded === 'false', `${tag}: scrim click must close the drawer`);

      // --- close button closes ---
      await toggle.tap(); await page.waitForTimeout(250);
      await page.locator('#calendar-close').tap(); await page.waitForTimeout(250);
      st = await state();
      check(st.expanded === 'false' && st.onToggle, `${tag}: Close button must close and return focus to the toggle`);

      // --- picking a day closes the drawer ---
      await toggle.tap(); await page.waitForTimeout(250);
      const day = page.locator('#archive-sidebar .fv-cal-day:not(:disabled)').first();
      await day.focus(); await page.keyboard.press('Enter'); await page.waitForTimeout(300);
      st = await state();
      check(st.expanded === 'false', `${tag}: choosing a day must close the drawer on a phone`);
      const filt = await page.evaluate(() => (typeof state !== 'undefined' && state.activeDateFilter) || null);
      if (filt !== null) check(/^\d{4}-\d{2}-\d{2}$/.test(String(filt)), `${tag}: a date filter was applied (${filt})`);
      check(errors.length === 0, `${tag}: page errors ${errors.join('; ').slice(0, 200)}`);
      await ctx.close();
    }
  } finally { await browser.close(); }
  if (failures.length) { console.error('mobile_calendar_smoke FAIL\n - ' + failures.join('\n - ')); process.exit(1); }
  console.log('mobile_calendar_smoke OK: 5 viewports x 2 themes; screenshots in ' + OUT);
}
main().catch(e => { console.error(e); process.exit(1); });
