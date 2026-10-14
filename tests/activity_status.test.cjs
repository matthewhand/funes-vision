/** Dependency-free, synthetic-only regression for the activity page connection status.
    The page must reuse the Activity v1 companion's strict contract validator. */
'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const ROOT=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(ROOT,'activity/index.html'),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script,'activity page inline script missing');
assert.match(html,/src="\/frontend\/static\/activity-v1\.js"/,'strict validator script not linked');

/** Minimal payload that satisfies the published activity v1 contract exactly. */
function validPayload(overrides={}){
  return Object.assign({
    schema_version:1,
    data_source:'watched-folder-preview',
    read_only:true,
    mode:'watched-folder-demo',
    inference:{object_detector:'not run',vision_decision_model:'not run'},
    scan:{max_frames:500,max_candidates_per_camera:2000,possibly_truncated:false,
          unsupported_filename_rule:'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,93}\\.jpe?g',skipped:{}},
    frames:[],visits:[],count:0,visit_count:0
  },overrides);
}

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
  const sandbox={
   document:{getElementById:element,querySelectorAll:()=>[]},
   fetch:async()=>mode==='http503'?{ok:false,status:503}:{ok:true,json:async()=>(mode==='malformed'?{unexpected:true}:validPayload())},
   AbortController,Date,setTimeout,clearTimeout,setInterval:()=>{},encodeURI,
   addEventListener(){}
  };
  // Load the real adapter so the page is proven to validate through the same
  // strict contract code, not a re-implementation or a stub.
  const shim={exports:{}};
  sandbox.window=sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(ROOT,'frontend/static/activity-v1.js'),'utf8'),
    sandbox,{filename:'activity-v1.js'});
  vm.runInContext(script,sandbox,{filename:'activity/index.html'});
  assert.equal(typeof sandbox.FunesActivityV1.normalizeActivity,'function',
    'strict activity-v1 validator must be exposed before the page script runs');
  return {element,setMode:value=>{mode=value;},refresh:()=>sandbox.refresh(),
    respondWith:payload=>{mode='custom';sandbox.fetch=async()=>({ok:true,json:async()=>payload});}};
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

test('strict contract violations are refused, not rendered with weaker checks',async()=>{
 const frame={id:'0123456789abcdef',camera:'DemoFront',filename:'a.jpg',
   image_url:'/captures/DemoFront/a.jpg',captured_at:'2026-10-12T10:14:18+00:00',
   capture_time_source:'filesystem-mtime',objects:[],description:'Unclassified local JPEG',
   annotation_source:'unclassified image',detector_status:'not run',
   decision_model_status:'not run',synthetic_fixture:false};
 const visit={id:'visit-1',camera:'DemoFront',start:frame.captured_at,end:frame.captured_at,
   frames:[frame.id],objects:[],frame_count:1};
 const withFrames=extra=>{
   const payload=validPayload({frames:[{...frame}],visits:[{...visit}],count:1,visit_count:1});
   return Object.assign(payload,extra);
 };
 for(const [name,payload] of [
   ['fictional fixture data cannot masquerade as watched-folder activity',
     withFrames({data_source:'fictional-data'})],
   ['a claimed model run is refused',
     withFrames({inference:{object_detector:'complete',vision_decision_model:'complete'}})],
   ['an external image host is refused',
     withFrames({frames:[{...frame,image_url:'https://external.invalid/a.jpg'}],visits:[{...visit}]})],
   ['an unclassified frame claiming detector objects is refused',
     withFrames({frames:[{...frame,objects:['person']}]})],
   ['a missing filename rule is refused',
     validPayload({scan:{max_frames:500,max_candidates_per_camera:2000,possibly_truncated:false,skipped:{}}})],
 ]){
const app=launch();await settle();
   app.respondWith(payload);
   await app.refresh();
   assert.equal(app.element('activity-data').classes.has('data-stale'),true,name);
   assert.doesNotMatch(app.element('frame-list').innerHTML,/<article class="frame">/,name+' must not render');
   assert.match(app.element('sync').textContent,/last successful update/,name);
 }
});

test('skipped watched-folder files are surfaced without hiding the frames',async()=>{
 const app=launch();await settle();
 app.respondWith(validPayload({scan:{max_frames:500,max_candidates_per_camera:2000,
   possibly_truncated:false,unsupported_filename_rule:'x',skipped:{'unsupported-filename':2}}}));
 await app.refresh();
 assert.equal(app.element('sync').textContent,'Watched folder connected');
 assert.equal(app.element('skipped-notice').hidden,false);
 assert.match(app.element('skipped-notice').textContent,/2 watched-folder file\(s\) skipped/);
});