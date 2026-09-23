const test=require('node:test');
const assert=require('node:assert/strict');
const {startMainHealthProbe}=require('../desktop/health.cjs');
test('main probe is quiet when disabled and records bounded summaries when enabled',()=>{
  let at=0,enabled=false; const records=[];
  const probe=startMainHealthProbe({state:()=>({enabled,queuedBytes:32,droppedChunks:2}),write:x=>records.push(x)},
    {now:()=>at,cpu:()=>({user:1000,system:2000}),rss:()=>1024,schedule:()=>({unref(){}}),cancel(){}});
  probe.tick(); at=100000;probe.tick();assert.equal(records.length,0);
  enabled=true;probe.tick();at+=1000;probe.tick();at+=6000;probe.tick();
  const summary=JSON.parse(records[0].slice('[main-perf] '.length));
  assert.equal(summary.timerLateMs,5000);assert.equal(summary.windowMs,7000);
  assert.equal(summary.queuedLogBytes,32);assert.equal(summary.droppedLogChunks,2);
  enabled=false;probe.tick();enabled=true;at+=100000;probe.tick();
  assert.equal(records.length,1,'re-enabling resets the timer baseline');
  probe.stop();
});
