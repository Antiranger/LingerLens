const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {extractFunction} = require('./player-harness.js');
const {createPlaybackProbe, createNetProbe} = require('../web-player/diagnostics-log.js');
function draftHarness() {
  const context = {subtitlePrefs:{mode:'both'},SUBTITLE_DRAFT_STALE_SECONDS:.5};
  vm.createContext(context);
  const source=fs.readFileSync(path.join(__dirname,'../web-player/player.js'),'utf8');
  for (const name of ['usableDraft','foldForDraft','draftLine']) vm.runInContext(extractFunction(source,name),context);
  return context;
}
test('prefetched text appears at its onset without another HTTP response',()=>{
  const h=draftHarness();
  const drafts=h.usableDraft([{text:'heard',tStart:10,tEnd:12},{text:'future',tStart:20,tEnd:22}]);
  assert.equal(h.draftLine(drafts,9.999,[]),null);
  assert.equal(h.draftLine(drafts,10,[]).source,'heard');
  assert.equal(h.draftLine(drafts,13,[]),null);
  assert.equal(h.draftLine(drafts,20,[]).source,'future');
});
test('a repeated phrase from another item is not hidden by an older cue hold',()=>{
  const h=draftHarness(), preview={text:'Again',tStart:10,tEnd:12,itemId:'new',generation:2};
  assert.equal(h.draftLine(preview,10,[{src:'Again',itemId:'old',generation:2}]).source,'Again');
  assert.equal(h.draftLine(preview,10,[{src:'Again',itemId:'new',generation:1}]).source,'Again');
  assert.equal(h.draftLine(preview,10,[{src:'Again',itemId:'new',generation:2}]),null);
});
test('passive frame instrumentation schedules nothing and respects a later wrapper',()=>{
  const previous=globalThis.requestAnimationFrame;
  let next=null,calls=0;
  const native=function(callback){assert.equal(typeof callback,'function');calls++;next=callback;return 7;};
  globalThis.requestAnimationFrame=native;
  const video={currentTime:0,buffered:{length:0},addEventListener(){},removeEventListener(){}};
  const probe=createPlaybackProbe({video,enabled:()=>true,emit(){},now:()=>0});
  try {
    probe.tick();assert.equal(calls,0);
    assert.throws(()=>globalThis.requestAnimationFrame(null));
    const observed=globalThis.requestAnimationFrame;
    const later=function(callback){return observed.call(this,callback);};
    globalThis.requestAnimationFrame=later;probe.dispose();
    assert.equal(globalThis.requestAnimationFrame,later);
    let receiver;const target={};later.call(target,function(){receiver=this;});
    next.call(target,100);assert.equal(receiver,target);
  } finally {probe.dispose();globalThis.requestAnimationFrame=previous;}
});
test('network capture is dormant when disabled and restores owned XHR methods',async()=>{
  class Xhr {open(){} send(){} addEventListener(){} }
  const nativeFetch=()=>Promise.resolve({ok:true,headers:{get:()=>10}});
  const open=Xhr.prototype.open,send=Xhr.prototype.send;
  const target={fetch:nativeFetch,XMLHttpRequest:Xhr};
  const probe=createNetProbe({target,enabled:()=>false});
  await target.fetch('/api/status');assert.equal(probe.takeWindow().req,0);
  probe.restore();assert.equal(target.fetch,nativeFetch);
  assert.equal(Xhr.prototype.open,open);assert.equal(Xhr.prototype.send,send);
});
test('decorative spinner never animates paint properties and footer defaults paused',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../web-player/style.css'),'utf8');
  const spinner=css.slice(css.indexOf('@keyframes spin-square'),css.indexOf('.loading-progress'));
  assert.doesNotMatch(spinner,/background|border-color|width:|height:/);
  assert.match(css,/animation: marquee 32s linear infinite paused/);
  assert.match(css,/\.document-hidden \.spinner/);
});
