#!/usr/bin/env node
'use strict';
// Multi-month/year archive + keyboard focus regression (#118 #119) on fiction-only JSON.
// No Ollama or live cameras; an explicit loopback fixture origin is required.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const ORIGIN = process.env.CALENDAR_NAV_URL || 'http://127.0.0.1:8899/';
if (!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(ORIGIN)) throw Error('refuse non-fixture URL');
const dates=['20241231','20250201','20260228','20260301','20260618'];
const images=dates.map((d,i)=>'10.0.0.21_01_'+d+'120000000_MOTDEC.jpg');
async function run() {
 const browser=await chromium.launch();
 try {
  for (const [name,width,height] of [['mobile',390,844],['desktop',1440,900]]) {
   const context=await browser.newContext({viewport:{width,height},reducedMotion:'reduce'});
   const page=await context.newPage();
   await page.route('**/images.json*',route=>route.fulfill({
      status:200,contentType:'application/json',body:JSON.stringify(images)}));
   await page.goto(ORIGIN,{waitUntil:'domcontentloaded'});
   await page.waitForSelector('#date-list .fv-cal-month',{state:'attached'});
   if (width<993) await page.locator('#sidebar-toggle').click();
   const chooser=page.locator('#date-list .fv-cal-month');
   assert.equal(await chooser.inputValue(),'2026-06');
   assert.equal(await chooser.locator('option').count(),5,'only months with recordings');
   await chooser.focus();
   await chooser.selectOption('2024-12');
   assert.equal(await chooser.inputValue(),'2024-12');
   assert(await chooser.evaluate(el=>document.activeElement===el),
      name+': selecting month must retain keyboard focus');
   assert(await page.locator('.fv-cal-arrow[data-cal-action="prev"]').isDisabled());
   const lastDay=page.locator('#date-list .fv-cal-day[data-date="2024-12-31"]');
   assert(await lastDay.isEnabled());
   assert.equal(await lastDay.getAttribute('aria-label'),'Tuesday 31 December 2024, 1 snapshot');
   await lastDay.focus();
   await page.keyboard.press('Enter');
   assert.equal(await page.locator('#date-list .fv-cal-day.is-selected').count(),1);
   if (width<993) {
     assert.equal(await page.locator('#sidebar-toggle').getAttribute('aria-expanded'),'false');
     assert(await page.locator('#sidebar-toggle').evaluate(el=>document.activeElement===el));
     await page.locator('#sidebar-toggle').click();
   } else {
     assert(await page.locator('.fv-cal-day[data-date="2024-12-31"]')
       .evaluate(el=>document.activeElement===el),'desktop day must retain focus');
   }
   await chooser.selectOption('2026-02');
   const prev=page.locator('.fv-cal-arrow[data-cal-action="prev"]');
   await prev.focus();
   await page.keyboard.press('Enter');
   assert.equal(await chooser.inputValue(),'2025-02');
   assert(await prev.evaluate(el=>document.activeElement===el),
     'previous arrow must preserve focus while still enabled');
   await page.keyboard.press('Enter');
   assert.equal(await chooser.inputValue(),'2024-12');
   assert(await chooser.evaluate(el=>document.activeElement===el),
     'when the previous arrow becomes disabled, focus moves to month chooser');
   const next=page.locator('.fv-cal-arrow[data-cal-action="next"]');
   await next.focus();
   await page.keyboard.press('Enter');
   assert.equal(await chooser.inputValue(),'2025-02');
   assert(await next.evaluate(el=>document.activeElement===el),
     'next arrow must preserve focus when still enabled');
   await chooser.selectOption('2026-02');
   const feb=page.locator('.fv-cal-day');
   assert.equal(await feb.count(),28,'non-leap February');
   const mar=page.locator('.fv-cal-arrow[data-cal-action="next"]');
   await mar.click();
   assert.equal(await chooser.inputValue(),'2026-03');
   const all=page.locator('.fv-cal-all');
   await all.focus();
   await page.keyboard.press('Enter');
   assert.equal(await all.getAttribute('aria-pressed'),'true');
   if (width<993) assert.equal(await page.locator('#sidebar-toggle').getAttribute('aria-expanded'),'false');
   console.log(name,'multi-year navigation, focus and selection OK');
   await context.close();
  }
  const ctx=await browser.newContext();
  const page=await ctx.newPage();
  await page.route('**/images.json*',route=>route.fulfill({
     status:200,contentType:'application/json',body:'[]'}));
  await page.goto(ORIGIN,{waitUntil:'domcontentloaded'});
  await page.waitForSelector('#date-list .fv-cal-month',{state:'attached'});
  assert(await page.locator('#date-list .fv-cal-month').isDisabled(),'empty archive must disable month chooser');
  assert.equal(await page.locator('.fv-cal-day.has-recordings').count(),0);
  console.log('empty archive OK');
  await ctx.close();
 } finally {await browser.close();}
}
run().catch(e=>{console.error(e);process.exitCode=1;});
