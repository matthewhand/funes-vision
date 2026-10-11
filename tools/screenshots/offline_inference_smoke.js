#!/usr/bin/env node
'use strict';
// A deliberately stopped Ollama / reasoning-model fixture (#120).
// Only the local synthetic stub may be used; this does not start inference.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const ORIGIN=process.env.OFFLINE_SMOKE_URL||'http://127.0.0.1:8899/';
if (!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(ORIGIN)) throw Error('offline smoke refuses non-fixture origin');
async function main() {
 const browser=await chromium.launch();
 const out=path.resolve('offline-previews');fs.mkdirSync(out,{recursive:true});
 try {
  for (const [name,width,height] of [['mobile',390,844],['desktop',1440,900]]) {
   for (const theme of ['dark','light']) {
    const context=await browser.newContext({viewport:{width,height},reducedMotion:'reduce'});
    await context.addInitScript(value=>localStorage.setItem('funes-vision.theme',value),theme);
    const page=await context.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(String(e)));
    await page.route('**/api/status*',async route=>{
      const res=await route.fetch();
      const s=await res.json();
      s.llm={...s.llm,reachable:false,model:null};
      s.inference={};
      if (s.queue) s.queue.deep_eta_s=0;
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(s)});
    });
    await page.goto(ORIGIN,{waitUntil:'domcontentloaded'});
    await page.waitForSelector('#date-list .fv-cal-month',{state:'attached'});
    await page.waitForFunction(()=>document.querySelector('#ai-live-label')?.textContent==='AI off',null,{timeout:10000});
    await page.locator('#btn-system-status').click();
    await page.waitForFunction(()=>document.querySelector('#system-status-content')?.textContent.includes('Inference: unavailable'),null,{timeout:10000});
    assert.match(await page.locator('#system-status-content').innerText(),/Inference:\s*unavailable/);
    await page.keyboard.press('Escape');
    for(const filter of ['all','objects','events']){
      await page.locator('#filter-tabs [data-filter="'+filter+'"]').evaluate(el=>el.click());
      await page.waitForTimeout(150);
    }
    if(width<=992){
      await page.locator('#sidebar-toggle').click();
      await page.locator('.fv-cal-all').click();
      assert.equal(await page.locator('#sidebar-toggle').getAttribute('aria-expanded'),'false');
    }else await page.locator('.fv-cal-all').click();
    assert.equal(await page.locator('html').getAttribute('data-theme'),theme);
    assert.equal(errors.length,0,'unexpected frontend JS errors: '+errors.join(' | '));
    await page.screenshot({path:path.join(out,name+'-'+theme+'.png'),animations:'disabled'});
    console.log(name,theme,'Ollama intentionally offline; browsing works');
    await context.close();
   }
  }
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
