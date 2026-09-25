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
test('the per-process breakdown appears only when a provider is supplied',()=>{
  const collect=(options)=>{
    let at=0; const records=[];
    const probe=startMainHealthProbe({state:()=>({enabled:true}),write:x=>records.push(x)},
      {now:()=>at,cpu:()=>({user:1000,system:2000}),rss:()=>2048,
       schedule:()=>({unref(){}}),cancel(){},...options});
    probe.tick(); at=1000; probe.tick(); at=7000; probe.tick(); probe.stop();
    return JSON.parse(records[0].slice('[main-perf] '.length));
  };
  // No provider: the summary keeps exactly the fields it had before, so an older
  // reader of the log sees no new key at all.
  assert.equal(collect({}).processes,undefined);
  const summary=collect({appMetrics:()=>[
    {pid:1,type:'Browser',cpuPct:140,wakeups:9000,wsMB:140},
    {pid:2,type:'GPU',cpuPct:60,wakeups:120,wsMB:300}]});
  assert.equal(summary.processes.length,2);
  assert.equal(summary.processes[0].wakeups,9000);
  // A breaking getAppMetrics must not cost us the window it was decorating.
  assert.equal(collect({appMetrics:()=>{throw new Error('no metrics')}}).timerLateMs,5000);
});
test('the proxy picture appears only when a provider is supplied, and never empties the window',()=>{
  const collect=(options)=>{
    let at=0; const records=[];
    const probe=startMainHealthProbe({state:()=>({enabled:true}),write:x=>records.push(x)},
      {now:()=>at,cpu:()=>({user:1000,system:2000}),rss:()=>2048,
       schedule:()=>({unref(){}}),cancel(){},...options});
    probe.tick(); at=1000; probe.tick(); at=7000; probe.tick(); probe.stop();
    return JSON.parse(records[0].slice('[main-perf] '.length));
  };
  assert.equal(collect({}).net,undefined,'no provider means no new key for an older reader');
  const summary=collect({netMetrics:()=>({n:12,kb:0,ch:4196,ms:940,paths:1,
    top:[['/media/seg/#.ts',2,0,4100,880,470]]})});
  assert.equal(summary.net.ch,4196,'the chunk count is the whole point of this section');
  assert.equal(summary.net.top[0][3],4100);
  // An idle window reports n=0 and is dropped rather than written as a zero row.
  assert.equal(collect({netMetrics:()=>({n:0,kb:0,ch:0,ms:0,paths:0,top:[]})}).net,undefined);
  // Same contract as appMetrics: a broken provider costs the section, not the window.
  assert.equal(collect({netMetrics:()=>{throw new Error('no picture')}}).timerLateMs,5000);
});
