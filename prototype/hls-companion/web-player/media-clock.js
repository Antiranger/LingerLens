/**
 * LingerLens MediaClock & Timeline Utilities
 *
 * Provides a unified wall clock for video playback derived from HLS PROGRAM-DATE-TIME (PDT).
 * NEVER falls back to Date.now() if PDT is not present.
 */

(function (global) {
  /**
   * Format epoch seconds into 24-hour HH:mm:ss in local time.
   * If epochSeconds is null/undefined/NaN, returns "--:--:--".
   * @param {number|null} epochSeconds
   * @returns {string}
   */
  function formatWallClockTime(epochSeconds) {
    if (epochSeconds == null || isNaN(epochSeconds) || !isFinite(epochSeconds)) {
      return "--:--:--";
    }
    const d = new Date(epochSeconds * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  /**
   * Creates a MediaClock instance.
   * @param {{ getHls: () => any, getVideo: () => HTMLVideoElement|null }} options
   */
  function createMediaClock(options = {}) {
    const getHls = options.getHls || options.hlsProvider || (() => null);
    const getVideo = options.getVideo || (() => options.videoElement || null);
    const getLevelDetails = options.levelDetailsProvider || (() => null);
    const fragments = () => {
      const hls = getHls();
      return hls?.levels?.[hls.currentLevel]?.details?.fragments
        || getLevelDetails()?.fragments || [];
    };
    const startOf = frag => Number.isFinite(frag.startPTS) ? frag.startPTS : frag.start;
    const pdtOf = frag => {
      if (frag.programDateTime == null) return null;
      const value = typeof frag.programDateTime === "number" ? frag.programDateTime
        : new Date(frag.programDateTime).getTime();
      return Number.isFinite(value) ? value / 1000 : null;
    };
    // undefined = no PDT intervals; null = measured timeline, but outside it.
    function mapPosition(position) {
      if (!Number.isFinite(position)) return null;
      const list = fragments();
      let known = false;
      for (let i = list.length - 1; i >= 0; --i) {
        const frag = list[i], start = startOf(frag), pdt = pdtOf(frag);
        if (pdt === null || !Number.isFinite(start) || !Number.isFinite(frag.duration)) continue;
        known = true;
        if (position >= start && position <= start + frag.duration + 1e-6) {
          return pdt + position - start;
        }
      }
      return known ? null : undefined;
    }
    function playingDateFallback() {
      const hls = getHls();
      return hls?.playingDate instanceof Date && Number.isFinite(hls.playingDate.getTime())
        ? hls.playingDate.getTime() / 1000 : null;
    }
    return {
      getPlayingWallTime() {
        const video = getVideo();
        if (!getHls() || !video) return null;
        const mapped = mapPosition(video.currentTime);
        return mapped === undefined ? playingDateFallback() : mapped;
      },
      getPlayingWallClock() { return this.getPlayingWallTime(); },
      playingWallTime() { return this.getPlayingWallTime(); },
      isWallTimeAvailable() { return this.getPlayingWallTime() != null; },
      wallTimeForMediaPosition(position) {
        const mapped = mapPosition(position);
        if (mapped !== undefined) return mapped;
        const current = playingDateFallback(), video = getVideo();
        return current !== null && video && Number.isFinite(position)
          ? current + position - video.currentTime : null;
      },

      /**
       * Format an epoch or null.
       */
      mediaPositionForWallTime(epochSeconds) {
        const epoch = Number(epochSeconds);
        if (!Number.isFinite(epoch)) return null;
        const list = fragments();
        let known = false;
        for (let i = list.length - 1; i >= 0; --i) {
          const frag = list[i], start = startOf(frag), pdt = pdtOf(frag);
          if (pdt === null || !Number.isFinite(start) || !Number.isFinite(frag.duration)) continue;
          known = true;
          if (epoch >= pdt && epoch <= pdt + frag.duration + 1e-6) return start + epoch - pdt;
        }
        if (known) return null;
        const current = playingDateFallback(), video = getVideo();
        return current !== null && video ? video.currentTime + epoch - current : null;
      },

      seekableWallClockRange() {
        const video = getVideo();
        if (!video?.seekable?.length) return null;
        const start = video.seekable.start(0);
        const end = video.seekable.end(video.seekable.length - 1);
        return {
          startPosition: start,
          endPosition: end,
          currentPosition: video.currentTime,
          startWallTime: this.wallTimeForMediaPosition(start),
          endWallTime: this.wallTimeForMediaPosition(end),
          currentWallTime: this.getPlayingWallTime(),
        };
      },

      formatTime(epochSeconds) {
        return formatWallClockTime(epochSeconds);
      },
    };
  }

  /*
   * isCueVisibleInTimeline, isLiveMessageVisibleInTimeline and a second
   * createFollowModeController used to live here. All three were unreachable in
   * the browser:
   *   - this file loads before workbench-controller.js (index.html:535 vs :538),
   *     whose createFollowModeController overwrites this one, and that is the
   *     DOM-aware implementation player.js actually uses;
   *   - the cue-visibility rule here accepted state "failed", directly
   *     contradicting the live rule in subtitle-scheduler.js:57 which rejects
   *     it. Two answers to one question is how a timeline and an overlay drift
   *     apart, so the stale copy is gone and subtitle-scheduler.js owns the rule.
   */

  const exported = {
    formatWallClockTime,
    createMediaClock,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = exported;
  } else {
    global.LingerLensMediaClock = exported;
    Object.assign(global, exported);
  }
})(typeof window !== "undefined" ? window : globalThis);
