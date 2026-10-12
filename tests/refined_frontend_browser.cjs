'use strict';
/* Optional local Playwright review: no model, external URL or camera access.
 * Run with PLAYWRIGHT executable path + NODE_PATH installed by reviewer.
 */
const {chromium}=require('playwright');
const cp=require('child_process');
const fs=require('fs');
const path=require('path');
const ROOT=path.resolve(__dirname,'..');
const PORT=18974;
const ORIGIN=`http://127.0.0.1:${PORT}`;
const dims=[[320,568],[360,800],[390,844],[768,1024],[844,390],[1024,600],[1366,768],[1440,900]];
async function main(){
 const service=cp.spawn('python3',['-m','http.server',String(PORT),'--bind','127.0.0.1','--directory',ROOT],{stdio:'ignore'});
 let browser;
 try{
  await new Promise(resolve=>setTimeout(resolve,550));
  browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE||undefined});
  const evidence=path.join(ROOT,'docs/evidence');fs.mkdirSync(evidence,{recursive:true});
  let passes=0;
  for(const [width,height] of dims) for(const theme of ['dark','light']){
   const page=await browser.newPage({viewport:{width,height},colorScheme:theme});
   const errors=[],unexpected=[];
   page.on('pageerror',e=>errors.push(e.message));
   page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
   page.on('request',q=>{if(!q.url().startsWith(ORIGIN+'/'))unexpected.push(q.url())});
   await page.goto(`${ORIGIN}/frontend/index.html`,{waitUntil:'load'});
   await page.locator('.card-img').first().waitFor({timeout:4500});
   const initial=await page.evaluate(()=>({title:document.querySelector('#camera-title')?.textContent,
    banner:document.querySelector('#synthetic-banner')?.textContent,
    count:document.querySelectorAll('.card-img').length,
    overflow:document.documentElement.scrollWidth-innerWidth,
    source:window.__FUNES_FIXTURE_ONLY__}));
   if(!initial.source||!initial.banner.includes('SYNTHETIC DEMO')||initial.count<1||initial.overflow>0||unexpected.length||errors.length)
      throw Error(JSON.stringify({viewport:`${width}x${height}`,theme,initial,unexpected,errors}));
   if([320,390,1440].includes(width))await page.screenshot({path:path.join(evidence,`refined-${width}x${height}-${theme}.png`),fullPage:true});
   // Front route reloads from scratch with only the public 6-frame catalog.
   await page.goto(`${ORIGIN}/frontend/index.html?camera=DemoFront`);
   await page.locator('.card-img').first().waitFor({timeout:4500});
   const frontCount=await page.evaluate(()=>document.querySelectorAll('.card-img').length);
   if(frontCount<1)throw Error('front sample failed');
   // Router must work as an actual static file URL, not server rewrite magic.
   await page.goto(`${ORIGIN}/frontend/index.html?camera=DemoBack`);
   await page.locator('.card-img').first().waitFor({timeout:4500});
   if((await page.locator('#camera-title').textContent()).trim()!=='Back')throw Error('back title');
   if((await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth))>0)throw Error('back overflow');
   // Test both fixture router directions and direct reload with dashboard.
   await page.goto(`${ORIGIN}/frontend/index.html?view=cameras`);
   await page.locator('.dashboard-card').first().waitFor({timeout:4500});
   if(await page.locator('.dashboard-card').count()!==2)throw Error('camera dashboard count');
   // Mutation must be denied, even if called directly without an application guard.
   const denied=await page.evaluate(async()=>{
     const r=await fetch('/api/delete',{method:'POST',body:'{}'});
     return {status:r.status,json:await r.json()};
   });
   if(denied.status!==403||!denied.json.error)throw Error('unsafe mutation response '+JSON.stringify(denied));
   if(errors.length||unexpected.length)throw Error(JSON.stringify({errors,unexpected}));
   console.log('PASS',`${width}x${height}`,theme,'timeline, dashboard, paths, deny-write, no-overflow');passes++;
   await page.close();
  }
  console.log('RESULT',`${passes}/${dims.length*2}`,'PASS');
 }finally{if(browser)await browser.close();service.kill('SIGTERM')}
}
main().catch(e=>{console.error(e.stack);process.exit(1)});
