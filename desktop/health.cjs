'use strict';
const { performance } = require('node:perf_hooks');

/** Small independent signal for main-loop starvation, not video frame timing. */
function startMainHealthProbe(log, {now=()=>performance.now(),
    cpu=previous=>process.cpuUsage(previous), rss=()=>process.memoryUsage().rss,
    schedule=setInterval, cancel=clearInterval, appMetrics=null, netMetrics=null} = {}) {
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
    // Per-process breakdown so a stalled main loop can be attributed to the
    // Browser process or to a child (GPU, Utility, a Tab) instead of "the app".
    // Injected rather than required: this module must stay loadable without
    // Electron so main-health.test.js can drive it with plain stubs.
    if (appMetrics) {
      // A probe must never be able to kill the thing it is observing, so a
      // throwing provider costs us the breakdown and nothing else.
      try {
        const processes=appMetrics();
        if (processes && processes.length) summary.processes=processes;
      } catch { }
    }
    // Same contract for the proxy picture: the whole point is to observe a
    // stalled main loop, so a broken counter must not be able to stop the
    // record that would have shown the stall.
    if (netMetrics) {
      try {
        const net=netMetrics();
        if (net && net.n) summary.net=net;
      } catch { }
    }
    log.write(`[main-perf] ${JSON.stringify(summary)}`+String.fromCharCode(10));
    start=at; baseline=cpu(); count=0; maxLate=0;
  }
  const timer=schedule(tick,1000);
  timer?.unref?.();
  return {tick,stop:()=>cancel(timer)};
}
module.exports={startMainHealthProbe};
