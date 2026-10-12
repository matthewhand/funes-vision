'use strict';
const fs=require('fs'),vm=require('vm'),path=require('path');
const base=path.resolve(__dirname,'../frontend');
const page=fs.readFileSync(path.join(base,'index.html'),'utf8');
const matches=[...page.matchAll(/<script>([\s\S]*?)<\/script>/g)];
if(matches.length!==1) throw Error('Expected exactly one inline SPA script, saw '+matches.length);
new vm.Script(matches[0][1],{filename:'frontend/index.html inline SPA'});
console.log('PASS: read-only public SPA inline script syntax');
