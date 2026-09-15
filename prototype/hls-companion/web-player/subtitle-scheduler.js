/*
 * LingerLens subtitle cue scheduler (redesign Fix D + Fix H).
 *
 * Pure, DOM-free decision logic so it can be unit-tested under node
 * (tests/test_cue_scheduler.js) and driven by player.js's 100ms tick.
 *
 * Contract:
 *  - A cue is displayable ONLY in state "done" with non-empty translation. "src"/"translating"
 *    never reach the screen; there is no "translating…" placeholder.
 *  - The display window is anchored at the sentence START: [tStart, tEnd + tail].
 *    The whole sentence appears when the speaker begins saying it and stays up
 *    for the entire utterance. `tail` only becomes visible during a genuine
 *    pause. Once later audio takes over, the previous cue's tail ends. Genuine
   *    audio overlaps from different speakers remain visible together. A later
   *    chunk from the same speaker replaces the older chunk, while distinct cues
   *    with the exact same start remain visible because their order is unknown.
 *  - A cue that becomes ready after the playhead passed its window is a "late
 *    cue": shown from now for `hold` seconds if it is late by at most
 *    `maxLateSeconds` (catchUp "show"), otherwise dropped and counted.
 *  - active() returns every independently-live cue in deterministic time order,
 *    so overlapping speech is rendered concurrently rather than hidden.
 *  - pick() remains the single-line compatibility interface: a replacement waits
 *    until the displayed cue dwelled at least `minDwell` seconds (or its own
 *    window ended). Tiny gaps (< bridgeGap) to the next cue do not blank it.
 */
(function (global) {
  "use strict";

  var DROPPED = { dropped: true };
  // Compatibility marker: cue.state === "done" || cue.state === "failed" is legacy input; failed cues remain hidden.

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
      return Boolean(cue) && cue.state === "done" && typeof cue.zh === "string" && cue.zh.trim().length > 0;
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
          admission = { from: t, until: t + cue.hold, catchUp: true };
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

    function pruneAdmissions(cues) {
      var retained = new Set();
      for (var index = 0; index < cues.length; index += 1) retained.add(cues[index].id);
      for (var id of admissions.keys()) {
        if (!retained.has(id)) admissions.delete(id);
      }
      if (currentId !== null && !retained.has(currentId)) {
        currentId = null;
        shownSince = null;
      }
    }

    function active(cues, t) {
      pruneAdmissions(cues);
      var visible = [];
      for (var index = 0; index < cues.length; index += 1) {
        var cue = cues[index];
        var admission = admit(cue, t);
        if (admission === undefined || admission === DROPPED) continue;
        if (t < admission.from || t > admission.until) continue;
        var cueStart = startOf(cue);
        var cueSpeaker = cue.speaker == null ? "" : String(cue.speaker).trim();
        var replacedBySameSpeaker = false;
        if (cueSpeaker) {
          for (var sameSpeakerIndex = 0; sameSpeakerIndex < cues.length; sameSpeakerIndex += 1) {
            var sameSpeakerSuccessor = cues[sameSpeakerIndex];
            if (sameSpeakerSuccessor.id === cue.id || !displayable(sameSpeakerSuccessor)) continue;
            if (String(sameSpeakerSuccessor.speaker == null ? "" : sameSpeakerSuccessor.speaker).trim() !== cueSpeaker) continue;
            var sameSpeakerStart = startOf(sameSpeakerSuccessor);
            if (sameSpeakerStart > t || sameSpeakerStart < cueStart) continue;
            if (t > sameSpeakerSuccessor.tEnd + Math.min(sameSpeakerSuccessor.hold, maxTail) + maxLateSeconds) continue;
            // A strictly later onset proves succession for one speaker. Equal
            // starts can be generated by Provider finalization/timestamp repair;
            // hiding either distinct cue would silently lose recognized text.
            if (sameSpeakerStart > cueStart) {
              replacedBySameSpeaker = true;
              break;
            }
          }
        }
        if (replacedBySameSpeaker) continue;
        var previousTailOwnedByLaterAudio = false;
        if (t >= cue.tEnd) {
          for (var successorIndex = 0; successorIndex < cues.length; successorIndex += 1) {
            var successor = cues[successorIndex];
            if (successor.id === cue.id || !displayable(successor)) continue;
            var successorStart = startOf(successor);
            if (t > successor.tEnd + Math.min(successor.hold, maxTail) + maxLateSeconds) continue;
            // Keep different-speaker rows only while their spoken ranges
            // overlap. After this cue's audio ends, any already-started cue
            // whose audio extends later owns the display.
            if (successorStart <= t && successor.tEnd > cue.tEnd) {
              previousTailOwnedByLaterAudio = true;
              // Catch-up is only for a gap in current speech. Once a newer
              // sentence takes over, do not resurrect this old caption later.
              if (admission.catchUp) admission.until = Math.min(admission.until, t);
              break;
            }
          }
        }
        if (!previousTailOwnedByLaterAudio) visible.push(cue);
      }
      visible.sort(function (left, right) {
        var byStart = startOf(left) - startOf(right);
        if (byStart) return byStart;
        var byEnd = left.tEnd - right.tEnd;
        if (byEnd) return byEnd;
        return String(left.id).localeCompare(String(right.id));
      });
      return visible;
    }

    function pick(cues, t) {
      var visible = active(cues, t);
      var best = visible.length ? visible[visible.length - 1] : null;

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

    function retime() {
      admissions.clear();
      currentId = null;
      shownSince = null;
    }

    function reset() {
      retime();
      stats.lateCues = 0;
      stats.droppedLateCues = 0;
      stats.sourceOnlyCues = 0;
    }

    return {
      active: active,
      pick: pick,
      retime: retime,
      reset: reset,
      get stats() { return Object.assign({}, stats); },
      debugState: function () { return { admissionCount: admissions.size }; },
    };
  }

  global.createSubtitleScheduler = createSubtitleScheduler;
  global.mergeSubtitleCueBySeq = mergeCueBySeq;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { createSubtitleScheduler: createSubtitleScheduler, mergeCueBySeq: mergeCueBySeq };
  }
})(typeof window !== "undefined" ? window : globalThis);
