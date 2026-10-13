/** Dependency-free, synthetic-only regression for the activity page connection status. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../activity/index.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script,'activity page inline script missing');

function launch(initialMode='ok'){
 const elements=new Map();
 function element(id){
  if(!elements.has(id)){
   const entry={textContent:'',innerHTML:'',hidden:true,classes:new Set(),addEventListener(){},setAttribute(){}};
   entry.classList={toggle(name,flag){if(flag)entry.classes.add(name);else entry.classes.delete(name);}};
   elements.set(id,entry);
  }
  return elements.get(id);
 }
 let mode=initialMode;
 const payload={count:0,visit_count:0,frames:[],visits:[]};
 const sandbox={
  document:{getElementById:element,querySelectorAll:()=>[]},
  fetch:async()=>mode==='http503'?{ok:false,status:503}:mode==='malformed'?{ok:true,json:async()=>({unexpected:true})}:{ok:true,json:async()=>payload},
  AbortController,Date,setTimeout,clearTimeout,setInterval:()=>{},encodeURI
 };
 vm.createContext(sandbox);
 vm.runInContext(script,sandbox,{filename:'activity/index.html'});
 return {element,setMode:value=>{mode=value;},refresh:()=>sandbox.refresh()};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('connection loss marks cached results stale and recovery clears warning',async()=>{
 const app=launch();await settle();
 assert.equal(app.element('sync').textContent,'Watched folder connected');
 app.setMode('http503');await app.refresh();
 assert.match(app.element('sync').textContent,/last successful update/);
 assert.equal(app.element('stale-notice').hidden,false);
 assert.ok(app.element('activity-data').classes.has('data-stale'));
 app.setMode('ok');await app.refresh();
 assert.equal(app.element('sync').textContent,'Watched folder connected');
 assert.equal(app.element('stale-notice').hidden,true);
 assert.equal(app.element('activity-data').classes.has('data-stale'),false);
 app.setMode('malformed');await app.refresh();
 assert.ok(app.element('activity-data').classes.has('data-stale'));
 app.setMode('ok');await app.refresh();
 assert.equal(app.element('stale-notice').hidden,true);
});

test('initial failure never misrepresents empty state as cached observations',async()=>{
 const app=launch('http503');await settle();
 assert.match(app.element('sync').textContent,/no data loaded/);
 assert.equal(app.element('stale-notice').hidden,true);
 app.setMode('ok');await app.refresh();
 assert.equal(app.element('sync').textContent,'Watched folder connected');
});
