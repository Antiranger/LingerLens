'use strict';
const { performance } = require('node:perf_hooks');

/** Small independent signal for main-loop starvation, not video frame timing. */
function startMainHealthProbe(log, {now=()=>performance.now(),
    cpu=previous=>process.cpuUsage(previous), rss=()=>process.memoryUsage().rss,
    schedule=setInterval, cancel=clearInterval} = {}) {
  let last=null, start=null, maxLate=0, count=0, baseline=null;
  function tick() {
    const at=now();
    if (!log.state().enabled) { last=start=baseline=null; count=0; maxLate=0; return; }
    if (last===null) { last=start=at; baseline=cpu(); return; }
    maxLate=Math.max(maxLate,Math.max(0,at-last-1000));
    last=at; count+=1;
    if(at-start<5000) return;
    const usage=cpu(baseline), state=log.state();
    const summary={windowMs:Math.round(at-start), timerLateMs:Math.round(maxLate), samples:count,
      cpuMs:(usage.user+usage.system)/1000, rssBytes:rss(),
      queuedLogBytes:state.queuedBytes||0, droppedLogChunks:state.droppedChunks||0};
    log.write(`[main-perf] ${JSON.stringify(summary)}`+String.fromCharCode(10));
    start=at; baseline=cpu(); count=0; maxLate=0;
  }
  const timer=schedule(tick,1000);
  timer?.unref?.();
  return {tick,stop:()=>cancel(timer)};
}
module.exports={startMainHealthProbe};
