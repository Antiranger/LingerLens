/*
 * LingerLens subtitle cue scheduler (redesign Fix D + Fix H).
 *
 * Pure, DOM-free decision logic so it can be unit-tested under node
 * (tests/test_cue_scheduler.js) and driven by player.js's 100ms tick.
 *
 * Contract:
 *  - A cue is displayable as soon as it has settled text: the translation when one
 *    exists, otherwise the ASR source text. Translation state decides which line has
 *    content, not whether the cue may be shown; there is still no "translating…"
 *    placeholder.
 *  - A cue that is already past maxLateSeconds when it first becomes displayable is
 *    still dropped, and a dropped cue is never revived by a later translation.
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
  // Compatibility marker: cue.state === "done" || cue.state === "failed" is legacy
  // input. Neither means "hidden": subtitleLines() decides what text a cue carries,
  // so a failed translation shows the source text it already has.

  function mergeCueBySeq(cues, incoming) {
    var current = cues.get(incoming.id);
    if (!current || !Number.isFinite(current.seq) || !Number.isFinite(incoming.seq) || incoming.seq > current.seq) {
      cues.set(incoming.id, incoming);
      return true;
    }
    return false;
  }

  // What a cue contributes to the screen. Only state "done" carries a translation;
  // a pending or failed cue keeps the source text it already has, so a sentence the
  // Provider never translated is read rather than left as a gap. `null` marks a cue
  // with no text at all -- the one case that must never render a row. Module scope
  // on purpose: the scheduler admits on this rule and player.js renders from it.
  function subtitleLines(cue) {
    if (!cue) return null;
    var translated = cue.state === "done" && typeof cue.zh === "string" ? cue.zh.trim() : "";
    var source = typeof cue.src === "string" ? cue.src.trim() : "";
    if (!translated && !source) return null;
    return { translated: translated, source: source };
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
    var lateTranslationPatchSeconds = Number.isFinite(opts.lateTranslationPatchSeconds)
      ? opts.lateTranslationPatchSeconds : 3.0;

    var admissions = new Map(); // cue.id -> {from, until} | DROPPED
    var admissionVersions = new Map(); // cue.id -> {hasTranslation}
    var stats = { lateCues: 0, droppedLateCues: 0, sourceOnlyCues: 0, lateTranslationRevives: 0 };
    var currentId = null;
    var shownSince = null;

    function displayable(cue) {
      return subtitleLines(cue) !== null;
    }

    function startOf(cue) {
      return Number.isFinite(cue.tStart) ? cue.tStart : cue.tEnd;
    }

    function admit(cue, t) {
      var admission = admissions.get(cue.id);
      var translated = cue.state === "done" && typeof cue.zh === "string" && !!cue.zh.trim();
      var version = admissionVersions.get(cue.id);
      var translationUpgrade = !!version && !version.hasTranslation && translated;
      var windowEnd = cue.tEnd + Math.min(cue.hold, maxTail);

      // A late translation revision is the one reason an old admission may be
      // reconsidered. Keep the grace short: patch the sentence the viewer just
      // read, never resurrect an old paragraph over newer speech.
      if (admission !== undefined && translationUpgrade && t <= windowEnd + lateTranslationPatchSeconds) {
        admissions.delete(cue.id);
        admission = undefined;
      }
      if (admission !== undefined) {
        admissionVersions.set(cue.id, { hasTranslation: translated });
        return admission;
      }
      if (!displayable(cue)) return undefined; // not ready yet; retry next tick
      if (t <= windowEnd) {
        // Ready before the speaker reaches this sentence -> wait for tStart.
        // Ready part-way through it -> show immediately for the remainder.
        admission = { from: Math.max(t, startOf(cue)), until: windowEnd };
      } else {
        var late = t - windowEnd;
        var lateLimit = translationUpgrade
          ? Math.max(maxLateSeconds, lateTranslationPatchSeconds) : maxLateSeconds;
        if (late <= lateLimit) {
          admission = { from: t, until: t + cue.hold, catchUp: true, translationPatch: translationUpgrade };
          stats.lateCues += 1;
          if (translationUpgrade) stats.lateTranslationRevives += 1;
        } else {
          admission = DROPPED;
          stats.droppedLateCues += 1;
        }
      }
      admissions.set(cue.id, admission);
      admissionVersions.set(cue.id, { hasTranslation: translated });
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
        if (!retained.has(id)) {
          admissions.delete(id);
          admissionVersions.delete(id);
        }
      }
      if (currentId !== null && !retained.has(currentId)) {
        currentId = null;
        shownSince = null;
      }
    }

    function active(cues, t) {
      pruneAdmissions(cues);
      // Build shared indexes once per render pass. The old implementation
      // scanned every retained cue for every current cue (twice), which made
      // the 10-minute subtitle history quadratic work on each 100ms tick.
      var candidates = [];
      var bySpeaker = new Map();
      for (var candidateIndex = 0; candidateIndex < cues.length; candidateIndex += 1) {
        var candidateCue = cues[candidateIndex];
        if (!displayable(candidateCue)) continue;
        var candidateAdmission = admit(candidateCue, t);
        if (candidateAdmission === undefined || candidateAdmission === DROPPED) continue;
        var candidate = {
          cue: candidateCue,
          admission: candidateAdmission,
          start: startOf(candidateCue),
          reach: candidateCue.tEnd + Math.min(candidateCue.hold, maxTail) + maxLateSeconds,
        };
        candidates.push(candidate);
        var candidateSpeaker = candidateCue.speaker == null ? "" : String(candidateCue.speaker).trim();
        // Providers without diarization expose one sequential recognition lane.
        // Estimated ranges can overlap; stacking those chunks lets a long old
        // row push current text outside the video. A later onset takes over.
        var lane = candidateSpeaker ? "speaker:" + candidateSpeaker
          : "undiarized:" + (candidateCue.generation == null ? "" : candidateCue.generation);
        {
          var speakerList = bySpeaker.get(lane);
          if (!speakerList) bySpeaker.set(lane, speakerList = []);
          speakerList.push(candidate);
        }
      }

      // For each cue, laterReach is the furthest valid window among strictly
      // later same-speaker cues that have already started at this playhead.
      // Grouping equal starts preserves the old strict `>` rule.
      var laterReach = new Map();
      for (var speakerList of bySpeaker.values()) {
        speakerList.sort(function (left, right) { return left.start - right.start; });
        var furthestReach = -Infinity;
        for (var end = speakerList.length - 1; end >= 0;) {
          var groupStart = speakerList[end].start;
          var groupEnd = end;
          while (groupEnd >= 0 && speakerList[groupEnd].start === groupStart) groupEnd -= 1;
          for (var groupIndex = groupEnd + 1; groupIndex <= end; groupIndex += 1) {
            laterReach.set(speakerList[groupIndex].cue.id, furthestReach);
          }
          if (groupStart <= t) {
            for (var reachIndex = groupEnd + 1; reachIndex <= end; reachIndex += 1) {
              furthestReach = Math.max(furthestReach, speakerList[reachIndex].reach);
            }
          }
          end = groupEnd;
        }
      }

      // The previous-tail rule only needs the longest eligible successor. Keep
      // the best two ids so the current cue itself can be excluded in O(1).
      var bestEnd = -Infinity, bestEndId = null;
      var secondEnd = -Infinity, secondEndId = null;
      for (var eligible of candidates) {
        if (eligible.start > t || eligible.reach < t) continue;
        var eligibleEnd = Number(eligible.cue.tEnd);
        if (eligibleEnd > bestEnd) {
          secondEnd = bestEnd; secondEndId = bestEndId;
          bestEnd = eligibleEnd; bestEndId = eligible.cue.id;
        } else if (eligible.cue.id !== bestEndId && eligibleEnd > secondEnd) {
          secondEnd = eligibleEnd; secondEndId = eligible.cue.id;
        }
      }

      var visible = [];
      for (var index = 0; index < candidates.length; index += 1) {
        var candidate = candidates[index];
        var cue = candidate.cue;
        var admission = candidate.admission;
        if (t < admission.from || t > admission.until) continue;
        // A strictly later onset proves succession for one speaker. Equal
        // starts can be generated by Provider finalization/timestamp repair;
        // hiding either distinct cue would silently lose recognized text.
        if (laterReach.get(cue.id) >= t) {
          if (admission.catchUp) admission.until = Math.min(admission.until, t);
          continue;
        }
        var previousTailOwnedByLaterAudio = false;
        if (t >= cue.tEnd) {
          var successorEnd = bestEndId !== cue.id ? bestEnd : secondEnd;
          // Keep different-speaker rows only while their spoken ranges
          // overlap. After this cue's audio ends, any already-started cue
          // whose audio extends later owns the display.
          previousTailOwnedByLaterAudio = successorEnd > cue.tEnd;
          // Catch-up is only for a gap in current speech. Once a newer
          // sentence takes over, do not resurrect this old caption later.
          if (previousTailOwnedByLaterAudio && admission.catchUp) admission.until = Math.min(admission.until, t);
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
      admissionVersions.clear();
      currentId = null;
      shownSince = null;
    }

    function reset() {
      retime();
      stats.lateCues = 0;
      stats.droppedLateCues = 0;
      stats.sourceOnlyCues = 0;
      stats.lateTranslationRevives = 0;
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
  global.subtitleLines = subtitleLines;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      createSubtitleScheduler: createSubtitleScheduler,
      mergeCueBySeq: mergeCueBySeq,
      subtitleLines: subtitleLines,
    };
  }
})(typeof window !== "undefined" ? window : globalThis);
