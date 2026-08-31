/*
 * LagLingo subtitle cue scheduler (redesign Fix D + Fix H).
 *
 * Pure, DOM-free decision logic so it can be unit-tested under node
 * (tests/test_cue_scheduler.js) and driven by player.js's 100ms tick.
 *
 * Contract:
 *  - A cue is displayable ONLY in state "done" or "failed". "src"/"translating"
 *    never reach the screen; there is no "translating…" placeholder.
 *  - The display window is anchored at the sentence START: [tStart, tEnd + tail].
 *    The whole sentence appears when the speaker begins saying it and stays up
 *    for the entire utterance. `tail` only becomes visible during a genuine
 *    pause: consecutive utterances are contiguous (one's tEnd is the next
 *    one's tStart), and pick() prefers the later cue, so the next line takes
 *    over immediately when there is no gap.
 *  - A cue that becomes ready after the playhead passed its window is a "late
 *    cue": shown from now for `hold` seconds if it is late by at most
 *    `maxLateSeconds` (catchUp "show"), otherwise dropped and counted.
 *  - The displayed cue is never instantly displaced: a replacement waits until
 *    the displayed cue dwelled at least `minDwell` seconds (or its own window
 *    ended). Tiny gaps (< bridgeGap) to the next cue do not blank the layer.
 */
(function (global) {
  "use strict";

  var DROPPED = { dropped: true };

  function mergeCueBySeq(cues, incoming) {
    var current = cues.get(incoming.id);
    if (!current || !Number.isFinite(current.seq) || !Number.isFinite(incoming.seq) || incoming.seq > current.seq) {
      cues.set(incoming.id, incoming);
      return true;
    }
    return false;
  }

  function createSubtitleScheduler(options) {
    var opts = options || {};
    // minDwell is deliberately short: measured utterances run 0.37s at the
    // shortest with a 1.6s median, so a long minimum dwell would pin a brief
    // line on screen and push every following line late.
    var minDwell = Number.isFinite(opts.minDwell) ? opts.minDwell : 0.6;
    var maxLateSeconds = Number.isFinite(opts.maxLateSeconds) ? opts.maxLateSeconds : 2.0;
    var bridgeGap = Number.isFinite(opts.bridgeGap) ? opts.bridgeGap : 0.3;
    var maxTail = Number.isFinite(opts.maxTail) ? opts.maxTail : 1.5;

    var admissions = new Map(); // cue.id -> {from, until} | DROPPED
    var stats = { lateCues: 0, droppedLateCues: 0, sourceOnlyCues: 0 };
    var currentId = null;
    var shownSince = null;

    function displayable(cue) {
      return Boolean(cue) && (cue.state === "done" || cue.state === "failed");
    }

    function startOf(cue) {
      return Number.isFinite(cue.tStart) ? cue.tStart : cue.tEnd;
    }

    function admit(cue, t) {
      var admission = admissions.get(cue.id);
      if (admission !== undefined) return admission;
      if (!displayable(cue)) return undefined; // not ready yet; retry next tick
      var windowEnd = cue.tEnd + Math.min(cue.hold, maxTail);
      if (t <= windowEnd) {
        // Ready before the speaker reaches this sentence -> wait for tStart.
        // Ready part-way through it -> show immediately for the remainder.
        admission = { from: Math.max(t, startOf(cue)), until: windowEnd };
      } else {
        var late = t - windowEnd;
        if (late <= maxLateSeconds) {
          admission = { from: t, until: t + cue.hold };
          stats.lateCues += 1;
        } else {
          admission = DROPPED;
          stats.droppedLateCues += 1;
        }
      }
      admissions.set(cue.id, admission);
      return admission;
    }

    function switchTo(cue, t) {
      if (!cue) {
        currentId = null;
        shownSince = null;
        return null;
      }
      if (cue.id !== currentId) {
        currentId = cue.id;
        shownSince = t;
        if (!cue.zh) stats.sourceOnlyCues += 1;
      }
      return cue;
    }

    function pick(cues, t) {
      var best = null;
      for (var index = 0; index < cues.length; index += 1) {
        var cue = cues[index];
        var admission = admit(cue, t);
        if (admission === undefined || admission === DROPPED) continue;
        if (t >= admission.from && t <= admission.until) {
          if (!best || cue.tEnd > best.tEnd) best = cue;
        }
      }

      if (currentId !== null && (!best || best.id !== currentId)) {
        var current = null;
        for (var j = 0; j < cues.length; j += 1) {
          if (cues[j].id === currentId) { current = cues[j]; break; }
        }
        var currentAdmission = current && admissions.get(currentId);
        var currentLive = current && currentAdmission && currentAdmission !== DROPPED
          && t >= currentAdmission.from && t <= currentAdmission.until;
        if (currentLive && best && best.tEnd > current.tEnd) {
          // Overlap: keep the displayed cue until it dwelled minDwell seconds.
          if (t - shownSince < minDwell) return current;
        } else if (!currentLive && !best && current) {
          // The displayed cue expired and nothing is due yet; bridge tiny gaps
          // to the next cue instead of flickering the layer blank.
          for (var k = 0; k < cues.length; k += 1) {
            var next = cues[k];
            if (!displayable(next)) continue;
            var nextAdmission = admissions.get(next.id);
            if (nextAdmission === DROPPED) continue;
            if (next.tEnd > t && next.tEnd - t <= bridgeGap) return current;
          }
        }
      }
      return switchTo(best, t);
    }

    function reset() {
      admissions.clear();
      stats.lateCues = 0;
      stats.droppedLateCues = 0;
      stats.sourceOnlyCues = 0;
      currentId = null;
      shownSince = null;
    }

    return {
      pick: pick,
      reset: reset,
      get stats() { return Object.assign({}, stats); },
    };
  }

  global.createSubtitleScheduler = createSubtitleScheduler;
  global.mergeSubtitleCueBySeq = mergeCueBySeq;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { createSubtitleScheduler: createSubtitleScheduler, mergeCueBySeq: mergeCueBySeq };
  }
})(typeof window !== "undefined" ? window : globalThis);
