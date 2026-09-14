const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { createSerialPoller } = require(path.resolve(__dirname, "../web-player/poll-loop.js"));

test("serial poller never overlaps slow runs", async () => {
  let active = 0;
  let peak = 0;
  let calls = 0;
  const poller = createSerialPoller({
    intervalMs: 1,
    run: async () => {
      active += 1;
      peak = Math.max(peak, active);
      calls += 1;
      await new Promise((resolve) => setTimeout(resolve, 8));
      active -= 1;
    },
  });
  poller.start();
  await new Promise((resolve) => setTimeout(resolve, 35));
  poller.stop();
  assert.equal(peak, 1);
  assert.ok(calls >= 2);
});

test("serial poller uses hidden cadence and wake refreshes immediately", async () => {
  let hidden = true;
  let calls = 0;
  const delays = [];
  const callbacks = [];
  const poller = createSerialPoller({
    intervalMs: 500,
    hiddenIntervalMs: 2000,
    isHidden: () => hidden,
    run: async () => { calls += 1; },
    setTimer(callback, delay) { callbacks.push(callback); delays.push(delay); return callbacks.length; },
    clearTimer() {},
  });
  poller.start();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(calls, 1);
  assert.equal(delays.at(-1), 2000);
  hidden = false;
  poller.wake();
  await Promise.resolve();
  assert.equal(calls, 2);
  poller.stop();
});

// Regression: wake() while run() was in flight used to fork a permanent chain.
// Both ticks reached the same `finally` and each assigned `timer = setTimer(...)`;
// the second overwrote the first handle, so the first chain became unreachable
// and stop() could never clear it. Measured before the fix: 5 wakes -> 6 live
// chains, stop() cleared 1, and 5 kept polling forever.
test("wake while a run is in flight does not fork a second timer chain", async () => {
  const timers = new Map();
  let nextId = 1;
  const liveChains = () => timers.size;
  const pending = [];
  let runCalls = 0;
  let active = 0;
  let peak = 0;

  const poller = createSerialPoller({
    intervalMs: 500,
    hiddenIntervalMs: 2000,
    isHidden: () => false,
    run() {
      runCalls += 1;
      active += 1;
      peak = Math.max(peak, active);
      return new Promise((resolve) => {
        pending.push(() => { active -= 1; resolve(); });
      });
    },
    setTimer(callback) { const id = nextId++; timers.set(id, callback); return id; },
    clearTimer(id) { timers.delete(id); },
  });

  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
  // Drain the tick cascade: settling one run can start the next.
  const drain = async () => {
    for (let i = 0; i < 20 && pending.length; i++) {
      while (pending.length) pending.shift()();
      await settle();
    }
  };

  poller.start(true);
  await settle();
  await drain();

  for (let i = 0; i < 10; i++) {
    // Never more than one armed chain, and never more than one run in flight.
    assert.ok(liveChains() <= 1, `wake #${i}: ${liveChains()} armed chains`);
    assert.ok(active <= 1, `wake #${i}: ${active} concurrent runs`);

    // Fire the armed timer, then wake() while that tick is awaiting run().
    const armed = [...timers.values()];
    timers.clear();
    armed.forEach((fn) => fn());
    await settle();
    poller.wake();          // lands mid-flight: must coalesce, not fork
    await settle();
    await drain();
  }

  assert.equal(peak, 1, "runs must never overlap");
  assert.equal(liveChains(), 1, "exactly one chain must remain armed");
  poller.stop();
  assert.equal(liveChains(), 0, "stop() must clear every chain");
  assert.equal(poller.isRunning(), false);
});

test("a wake during a run still causes another run", async () => {
  let release = null;
  let calls = 0;
  const poller = createSerialPoller({
    intervalMs: 500,
    run() {
      calls += 1;
      return new Promise((resolve) => { release = resolve; });
    },
    setTimer: () => 1,
    clearTimer: () => {},
  });
  poller.start(true);
  await Promise.resolve();
  assert.equal(calls, 1);
  poller.wake();            // in flight: must be remembered, not dropped
  release();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(calls, 2, "the coalesced wake must run again");
  poller.stop();
});
