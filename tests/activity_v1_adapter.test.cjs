'use strict';
/* Offline synthetic regression: uses only bundled fictional JPEGs in a temp dir. */
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const vm = require('node:vm');
const { runPython } = require('./harness.cjs');
const ROOT = path.resolve(__dirname, '..');
// The project uses ESM package mode, but this browser script must also be
// exercisable without a test-only bundle or dependency installation.
const moduleShim = {exports:{}};
vm.runInNewContext(fs.readFileSync(path.join(ROOT,'frontend/static/activity-v1.js'),'utf8'),
  {module:moduleShim,AbortController,setTimeout,clearTimeout,Date},
  {filename:'activity-v1.js'});
const {normalizeActivity, mount} = moduleShim.exports;

function publishedSyntheticPayload() {
  const code = [
    'import contextlib, io, json, os, tempfile',
    'from pathlib import Path',
    'from tools.seed_synthetic_watch import seed',
    'from funes.server import SnapshotCatalog',
    'with tempfile.TemporaryDirectory(prefix="funes-v1-fictional-") as tmp:',
    '    with contextlib.redirect_stdout(io.StringIO()): seed(Path(tmp))',
    '    print(json.dumps(SnapshotCatalog(Path(tmp)).snapshot()))'
  ].join('\n');
  const run = runPython(['-B', '-c', code], { cwd: ROOT });
  assert.equal(run.status, 0, run.stderr);
  return JSON.parse(run.stdout.trim());
}
const payload = publishedSyntheticPayload();
const clone = () => JSON.parse(JSON.stringify(payload));
const bad = (name, transform, pattern=/Invalid activity-v1 response/) => {
  test('fail-closed: ' + name, () => {
    const altered = clone();
    transform(altered);
    assert.throws(() => normalizeActivity(altered), pattern);
  });
};

test('Python actual synthetic v1 payload: 10 JPEGs / 7 visits, no real inference', () => {
  const result = normalizeActivity(payload);
  assert.equal(result.count, 10);
  assert.equal(result.visit_count, 7);
  assert.ok(result.frames.every(f => f.synthetic_fixture && f.capture_time_source==='synthetic-fixture'));
  assert.equal(result.frames.filter(f=>f.camera==='DemoFront').length, 6);
  assert.equal(result.frames.filter(f=>f.camera==='DemoBack').length, 4);
  assert.equal(result.possibly_truncated, false);
});
bad('wrong schema', p => { p.schema_version=2; });
bad('fixture API cannot masquerade as activity', p => { p.data_source='fictional-data'; });
bad('write-enabled contract refused', p => { p.read_only=false; });
bad('fake model run', p => { p.inference.object_detector='complete'; });
bad('untrusted external image URL', p => { p.frames[0].image_url='https://external.invalid/x.jpg'; });
bad('path traversal', p => { p.frames[0].filename='..jpg'; });
bad('unclassifiable capture provenance', p => { p.frames[0].capture_time_source='camera-exif'; });
bad('missing timestamp timezone', p => { p.frames[0].captured_at='2026-10-12T10:14:18'; });
bad('wrong count', p => { p.count+=1; });
bad('unknown visit member', p => { p.visits[0].frames[0]='badid'; });
bad('visit falsely indicates another camera', p => { p.visits[0].camera=p.visits[0].camera==='DemoFront'?'DemoBack':'DemoFront'; });
bad('visit invents an object', p => { p.visits[0].objects.push('human-entered-home'); });
bad('bad scan bounds', p => { p.scan.max_frames=9000; });
bad('duplicate visit ID', p => { p.visits[1].id=p.visits[0].id; });
bad('unclassified image must not pretend to be detected', p => {
  const f=p.frames[0];f.synthetic_fixture=false;f.capture_time_source='filesystem-mtime';
  f.annotation_source='unclassified image';f.objects=['person'];
});

class FakeNode {
  constructor(tag='div') { this.tagName=tag; this.children=[]; this.handlers={};this.textContent='';this.hidden=true;this.dataset={};this.attributes={};this.disabled=false; }
  append(...children){ this.children.push(...children); }
  replaceChildren(...children){ this.children=[...children]; }
  addEventListener(event, handler){ this.handlers[event]=handler; }
  setAttribute(key,value){ this.attributes[key]=value; }
  click(){ this.handlers.click?.(); }
}
function fakeDocument() {
  const ids = new Map(), cameras=['all','DemoFront','DemoBack'].map(camera=>{
    const item=new FakeNode('button');item.dataset.camera=camera;return item;
  });
  return {
    getElementById(id){if(!ids.has(id))ids.set(id,new FakeNode());return ids.get(id);},
    createElement(tag){return new FakeNode(tag);},
    querySelectorAll(s){return s==='[data-camera]'?cameras:[];},
    cameras
  };
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('strictly same-origin GET, separated camera filters, offline stale state and recovery', async () => {
  const doc=fakeDocument();
  let mode='ok';const requests=[];
  const api=async(path, opts)=>{
    requests.push({path,method:opts.method,cache:opts.cache,credentials:opts.credentials,redirect:opts.redirect});
    if(mode==='error')throw new Error('offline');
    if(mode==='bad')return {ok:true,json:async()=>({status:'fixture'})};
    return {ok:true,json:async()=>clone()};
  };
  const controller=mount({document:doc,fetchActivity:api});
  await settle();
  assert.equal(doc.getElementById('frame-count').textContent,'10');
  assert.equal(doc.getElementById('visit-count').textContent,'7');
  assert.equal(doc.getElementById('source-status').dataset.state,'ready');
  assert.equal(doc.getElementById('stale-notice').hidden,true);
  assert.deepEqual(requests[0],{path:'/api/activity',method:'GET',cache:'no-store',
    credentials:'same-origin',redirect:'error'});
  const first=doc.getElementById('activity-frames').children[0];
  assert.match(first.children[1].children[2].textContent,/Synthetic fixture annotation/);
  assert.ok(first.children[0].src.startsWith('/captures/'));
  doc.cameras[1].click();
  assert.equal(doc.getElementById('frame-count').textContent,'6');
  assert.equal(doc.cameras[1].attributes['aria-pressed'],'true');
  mode='error'; await controller.refresh();
  assert.equal(doc.getElementById('source-status').dataset.state,'stale');
  assert.equal(doc.getElementById('stale-notice').hidden,false);
  assert.equal(doc.getElementById('frame-count').textContent,'6');
  mode='bad'; await controller.refresh();
  assert.equal(doc.getElementById('source-status').dataset.state,'stale');
  mode='ok'; await controller.refresh();
  assert.equal(doc.getElementById('source-status').dataset.state,'ready');
  assert.equal(doc.getElementById('stale-notice').hidden,true);
});
test('initial incompatible API never loads fixture content', async()=>{
  const doc=fakeDocument();
  mount({document:doc,fetchActivity:async()=>({ok:true,json:async()=>({fixture:true})})});
  await settle();
  assert.equal(doc.getElementById('source-status').dataset.state,'unavailable');
  assert.equal(doc.getElementById('frame-count').textContent,'');
  assert.equal(doc.getElementById('activity-frames').children.length,0);
});
test('separate default fixture bridge remains present and isolated',()=>{
  const original=fs.readFileSync(path.join(ROOT,'frontend/static/fixture-api.js'),'utf8');
  const index=fs.readFileSync(path.join(ROOT,'frontend/index.html'),'utf8');
  assert.match(original,/window\.__FUNES_FIXTURE_ONLY__ = true/);
  assert.match(index,/<script src="static\/fixture-api\.js"><\/script>/);
  assert.match(index,/href="activity-v1\.html"/);
  assert.doesNotMatch(index,/<script src="static\/activity-v1\.js"><\/script>/);
});
test('the original activity page reuses this strict validator, not a weak copy',()=>{
  const timeline=fs.readFileSync(path.join(ROOT,'activity/index.html'),'utf8');
  assert.match(timeline,/src="\/frontend\/static\/activity-v1\.js"/);
  assert.match(timeline,/FunesActivityV1\.normalizeActivity/);
  assert.doesNotMatch(timeline,/Array\.isArray\(data\.frames\)/);
});

/** Give one frame a name the shared eligibility rule rejects. */
function withUnsupportedName(name){
  const altered=clone();
  const frame=altered.frames[0];
  frame.filename=name;
  frame.image_url='/captures/'+frame.camera+'/'+name;
  for(const visit of altered.visits) visit.frames=visit.frames.filter(id=>id!==frame.id);
  altered.visits=altered.visits.filter(v=>v.frames.length);
  altered.visits.forEach((v,i)=>{v.id='visit-'+(i+1);v.frame_count=v.frames.length;});
  altered.visit_count=altered.visits.length;
  return altered;
}
for(const name of ['front door 01.jpg','porte-Ünterwegs-01.jpg','.hidden.jpg','a..b.jpg','x'.repeat(120)+'.jpg']){
  test('one unsupported filename never fails the whole response: '+name,()=>{
    const altered=withUnsupportedName(name);
    const result=normalizeActivity(altered);
    assert.equal(result.unrenderable_filenames,1);
    assert.equal(result.count,9,'the other nine frames stay visible');
    assert.equal(result.visit_count,altered.visits.length);
    assert.ok(result.frames.every(f=>!f.filename.includes(name)));
    assert.ok(result.visits.every(v=>v.frame_count===v.frame_ids.length));
  });
}
test('server-reported skip accounting is surfaced and bounded',()=>{
  const altered=clone();
  altered.scan.skipped={'unsupported-filename':3,'incomplete-or-unstable-jpeg':1};
  assert.equal(normalizeActivity(altered).server_skipped,4);
  assert.equal(normalizeActivity(clone()).server_skipped,0);
});
bad('skip accounting must be present', p => { delete p.scan.skipped; });
bad('skip accounting must not be invented', p => { p.scan.skipped={'x':-1}; });
bad('skip accounting must stay a plain object', p => { p.scan.skipped=[1]; });
bad('the shared filename rule must be published', p => { delete p.scan.unsupported_filename_rule; });

test('the view reports skipped files and still renders the rest', async()=>{
  const doc=fakeDocument();
  const altered=clone();
  altered.scan.skipped={'unsupported-filename':2};
  mount({document:doc,fetchActivity:async()=>({ok:true,json:async()=>altered})});
  await settle();
  assert.equal(doc.getElementById('source-status').dataset.state,'ready');
  assert.equal(doc.getElementById('frame-count').textContent,'10');
  assert.equal(doc.getElementById('skipped-notice').hidden,false);
  assert.match(doc.getElementById('skipped-notice').textContent,/2 watched-folder files were skipped/);
});

test('the adapter only mounts on a page that provides its own layout',()=>{
  const browserSandbox={
    document:{getElementById:()=>null,querySelectorAll:()=>[]},
    fetch:async()=>({ok:true,json:async()=>({})}),
    AbortController,setTimeout,clearTimeout,Date,
    addEventListener:(event,handler)=>{browserSandbox.handlers[event]=handler;}
  };
  browserSandbox.handlers={};
  browserSandbox.window=browserSandbox;
  vm.runInNewContext(fs.readFileSync(path.join(ROOT,'frontend/static/activity-v1.js'),'utf8'),
    browserSandbox,{filename:'activity-v1.js'});
  assert.equal(typeof browserSandbox.FunesActivityV1.normalizeActivity,'function');
  browserSandbox.handlers.DOMContentLoaded();
  assert.deepEqual(browserSandbox.lastStatus,undefined,
    'a host page with its own markup must not be taken over by the companion');
});
