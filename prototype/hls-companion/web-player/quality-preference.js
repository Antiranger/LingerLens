(function (global) {
  "use strict";

  // Picks the default quality after a probe. Rules:
  // - only browser-compatible options (requiresTranscode stays user-forced),
  // - never auto-pick above 1080p (4K stays an explicit user choice),
  // - heights descend 1080 -> 720 -> lower,
  // - within one height tier prefer separate v+a legs over a muxed single leg:
  //   a single connection to the CDN tops out below what high-bitrate muxed
  //   renditions need (docs/perf-mouse-lag-analysis-2026-09-09.md, P1), so a
  //   muxed default chronically falls behind the live edge.
  function pickPreferredQuality(qualities) {
    var compatible = (qualities || []).filter(function (q) { return q && !q.requiresTranscode; });
    if (!compatible.length) return null;
    var heights = [];
    for (var i = 0; i < compatible.length; i += 1) {
      var h = compatible[i].height || 0;
      if (h > 0 && h <= 1080 && heights.indexOf(h) < 0) heights.push(h);
    }
    heights.sort(function (a, b) { return b - a; });
    for (var j = 0; j < heights.length; j += 1) {
      var tier = compatible.filter(function (q) { return (q.height || 0) === heights[j]; });
      for (var k = 0; k < tier.length; k += 1) {
        if (tier[k].separateAudio) return tier[k];
      }
      if (tier.length) return tier[0];
    }
    return null;
  }

  global.pickPreferredQuality = pickPreferredQuality;
  if (typeof module !== "undefined" && module.exports) module.exports = { pickPreferredQuality: pickPreferredQuality };
})(typeof window !== "undefined" ? window : globalThis);
