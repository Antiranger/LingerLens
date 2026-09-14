(function (global) {
  "use strict";

  /*
   * One serial poller: never two runs of `run()` in flight, and never more than
   * one armed timer.
   *
   * The previous `wake()` cleared `timer` and called `tick()` unconditionally.
   * `tick` is guarded only by `running` and `generation`, neither of which
   * `wake()` changes, so a wake arriving while `run()` was still in flight
   * started a SECOND concurrent tick. Both ticks then reached the `finally`
   * below and each assigned `timer = setTimer(...)`; the second assignment
   * overwrote the first handle, so the first chain became unreachable and
   * `stop()` could never clear it.
   *
   * Measured before the fix: 5 visibilitychange wakes produced 6 concurrent
   * timer chains, `stop()` cleared 1, and the remaining 5 kept polling forever.
   * Since player.js:1880-1883 wakes both pollers on every tab switch, request
   * rate grew without bound and hammered the single-threaded Companion.
   *
   * A wake now sets a flag that the running tick consumes, so waking is
   * idempotent and chain count stays at exactly one.
   */
  function createSerialPoller(options) {
    var opts = options || {};
    var run = opts.run;
    var intervalMs = Math.max(0, Number(opts.intervalMs || 0));
    var hiddenIntervalMs = Math.max(intervalMs, Number(opts.hiddenIntervalMs || intervalMs));
    var isHidden = opts.isHidden || function () { return false; };
    var setTimer = opts.setTimer || global.setTimeout.bind(global);
    var clearTimer = opts.clearTimer || global.clearTimeout.bind(global);
    var timer = null;
    var running = false;
    var generation = 0;
    var inFlight = false;
    var pendingWake = false;

    function arm(expectedGeneration) {
      if (!running || expectedGeneration !== generation) return;
      // Only ever hold one armed timer for this generation.
      if (timer !== null) clearTimer(timer);
      var delay = isHidden() ? hiddenIntervalMs : intervalMs;
      timer = setTimer(function () { tick(expectedGeneration); }, delay);
    }

    async function tick(expectedGeneration) {
      if (!running || expectedGeneration !== generation) return;
      if (inFlight) {
        // Another run owns the loop; ask it to go again as soon as it can.
        pendingWake = true;
        return;
      }
      timer = null;
      inFlight = true;
      try {
        await run();
      } finally {
        inFlight = false;
        if (!running || expectedGeneration !== generation) {
          pendingWake = false;
          return;
        }
        if (pendingWake) {
          pendingWake = false;
          tick(expectedGeneration);
          return;
        }
        arm(expectedGeneration);
      }
    }

    return {
      start: function (immediate) {
        if (running) return;
        running = true;
        generation += 1;
        var expectedGeneration = generation;
        if (immediate !== false) tick(expectedGeneration);
        else arm(expectedGeneration);
      },
      stop: function () {
        running = false;
        generation += 1;
        pendingWake = false;
        if (timer !== null) clearTimer(timer);
        timer = null;
      },
      wake: function () {
        if (!running) return;
        if (inFlight) {
          // Never fork a second chain; coalesce into the running one.
          pendingWake = true;
          return;
        }
        if (timer !== null) clearTimer(timer);
        timer = null;
        tick(generation);
      },
      isRunning: function () { return running; },
      /** Test/diagnostic view: number of armed timer chains. */
      armedChains: function () { return timer === null ? 0 : 1; },
    };
  }

  global.createSerialPoller = createSerialPoller;
  if (typeof module !== "undefined" && module.exports) module.exports = { createSerialPoller: createSerialPoller };
})(typeof window !== "undefined" ? window : globalThis);
