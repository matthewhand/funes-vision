/** Optional low-load Chromium proof of watched-folder UI, using real Python API. */
'use strict';
const {chromium}=require('playwright');
const cp=require('child_process');
const fs=require('fs');
const path=require('path');
const os=require('os');
(async()=>{
 const root=path.resolve(__dirname,'..');
 const incoming=fs.mkdtempSync(path.join(os.tmpdir(),'funes-activity-'));
 const port=18882;
 const origin=`http://127.0.0.1:${port}`;
 const server=cp.spawn('python3',['-m','funes.server','--watch-dir',incoming,'--port',String(port)],{cwd:root,stdio:'ignore'});
 let browser;
 try{
  await new Promise(r=>setTimeout(r,500));
  browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE});
  const screenshots=path.join(root,'docs/evidence');fs.mkdirSync(screenshots,{recursive:true});
  for(const [w,h] of [[1366,900],[390,844]]){
   const page=await browser.newPage({viewport:{width:w,height:h},colorScheme:'dark'});
   const errors=[],external=[];
   page.on('pageerror',e=>errors.push(e.message));
   page.on('request',r=>{if(!r.url().startsWith(origin+'/'))external.push(r.url())});
   await page.goto(origin+'/');
   if(w===1366){
    if((await page.locator('#frames').textContent())!=='0')throw Error('Before ingestion must be empty');
    await page.screenshot({path:path.join(screenshots,'folder-activity-00-empty.png')});
    const seed=cp.spawnSync('python3',['tools/seed_synthetic_watch.py','--dest',incoming],{cwd:root,encoding:'utf8'});
    if(seed.status!==0)throw Error('Seed failure '+seed.stderr);
   }
   await page.waitForFunction(()=>document.querySelector('#frames')?.textContent==='10',null,{timeout:7000});
   if((await page.locator('#visits').textContent())!=='7')throw Error('Visit grouping UI failed');
   if((await page.locator('.frame').count())!==10)throw Error('Frame UI count');
   await page.screenshot({path:path.join(screenshots,w===390?'folder-activity-mobile.png':'folder-activity-desktop.png'),fullPage:true});
   if(w===1366){await page.getByRole('button',{name:'Front entrance'}).click();await page.waitForTimeout(160);await page.screenshot({path:path.join(screenshots,'folder-activity-visitor-filter.png')});await page.getByRole('button',{name:'All cameras'}).click();}
   const r=await page.evaluate(async()=>{const response=await fetch('/api/activity');return response.json()});
   if(r.frames.length!==10||r.visits.length!==7||r.inference.object_detector!=='not run')throw Error('Server API result');
   if(errors.length||external.length)throw Error(JSON.stringify({errors,external}));
   console.log(`PASS ${w}x${h}: empty → 10 JPEGs → 7 visits; no external requests`);
   await page.close();
  }
 }finally{if(browser)await browser.close();server.kill('SIGTERM');fs.rmSync(incoming,{recursive:true,force:true})}
})().catch(e=>{console.error(e.stack);process.exit(1)});
