const test=require('node:test');
const assert=require('node:assert/strict');
const {createSubtitleRenderLoop}=require('../web-player/subtitle-render-loop.js');
function harness({supported=true}={}) {
 let callback=null,timer=null,now=0,hidden=false,stopped=false;const seen=[];
 const video=supported?{requestVideoFrameCallback(cb){callback=cb;return 7;},cancelVideoFrameCallback(id){assert.equal(id,7);stopped=true;}}:{};
 const loop=createSubtitleRenderLoop({video,render:t=>seen.push(t),now:()=>now,isHidden:()=>hidden,
  setInterval(cb){timer=cb;return 8;},clearInterval(id){assert.equal(id,8);}});
 return {seen,loop,frame(t){callback(now,{mediaTime:t});},tick(t){now=t;timer();},hidden(v){hidden=v;},stopped:()=>stopped};
}
test('paint uses the presented media frame, never a positive lookahead',()=>{
 const h=harness();h.frame(12.04);assert.deepEqual(h.seen,[12.04]);h.tick(100);assert.equal(h.seen.length,1);
 h.frame(12.08);assert.deepEqual(h.seen,[12.04,12.08]);h.loop.stop();
});
test('paused or stalled frames still admit a subsequently arriving translation',()=>{
 const h=harness();h.frame(3);h.tick(200);assert.deepEqual(h.seen,[3,undefined]);h.loop.stop();
});
test('hidden pages do not paint and stop cancels the owned video callback',()=>{
 const h=harness();h.hidden(true);h.frame(5);h.tick(300);assert.deepEqual(h.seen,[]);
 h.loop.stop();assert.equal(h.stopped(),true);h.frame(6);assert.deepEqual(h.seen,[]);
});
test('older browsers keep the bounded fallback with no early offset',()=>{
 const h=harness({supported:false});h.tick(100);assert.deepEqual(h.seen,[undefined]);h.loop.stop();
});
