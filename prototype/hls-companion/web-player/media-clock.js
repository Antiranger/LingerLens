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
    return {
      /**
       * Returns the wall clock time (in seconds) corresponding to current video playback,
       * or null if PDT is not available.
       * @returns {number|null}
       */
      getPlayingWallTime() {
        const hls = getHls ? getHls() : null;
        const video = getVideo ? getVideo() : null;
        if (!hls || !video) return null;

        // 1. Prefer hls.playingDate
        if (hls.playingDate instanceof Date && !isNaN(hls.playingDate.getTime())) {
          return hls.playingDate.getTime() / 1000;
        }

        // 2. Fallback to fragment.programDateTime + (video.currentTime - fragment.start)
        const currentLevel = hls.currentLevel;
        const levelDetails = getLevelDetails() || hls.levels?.[currentLevel]?.details;
        const fragments = levelDetails?.fragments;
        if (fragments && fragments.length > 0) {
          const ct = video.currentTime;
          // Find the fragment containing currentTime, or the closest prior fragment
          let targetFrag = null;
          for (const frag of fragments) {
            if (frag.start <= ct && ct <= frag.start + frag.duration + 0.5) {
              targetFrag = frag;
              break;
            }
          }
          if (!targetFrag) {
            // Find latest fragment started before ct
            for (let i = fragments.length - 1; i >= 0; i--) {
              if (fragments[i].start <= ct) {
                targetFrag = fragments[i];
                break;
              }
            }
          }
          if (targetFrag && targetFrag.programDateTime != null) {
            const pdtMs = typeof targetFrag.programDateTime === "number"
              ? targetFrag.programDateTime
              : new Date(targetFrag.programDateTime).getTime();
            if (!isNaN(pdtMs)) {
              return (pdtMs / 1000) + (ct - targetFrag.start);
            }
          }
        }

        // PDT is unavailable - do not guess with Date.now()!
        return null;
      },

      /**
       * Returns whether wall time is currently available via PDT.
       * @returns {boolean}
       */
      getPlayingWallClock() {
        return this.getPlayingWallTime();
      },

      playingWallTime() {
        return this.getPlayingWallTime();
      },

      isWallTimeAvailable() {
        return this.getPlayingWallTime() != null;
      },

      /**
       * Maps a media position (in video.currentTime seconds) to wall clock seconds.
       * wallTimeForMediaPosition(position) = current Playback Wall Time + (position - video.currentTime)
       * @param {number} position
       * @returns {number|null}
       */
      wallTimeForMediaPosition(position) {
        const pwt = this.getPlayingWallTime();
        const video = getVideo ? getVideo() : null;
        if (pwt == null || !video || isNaN(position)) return null;
        return pwt + (position - video.currentTime);
      },

      /**
       * Format an epoch or null.
       */
      mediaPositionForWallTime(epochSeconds) {
        const current = this.getPlayingWallTime();
        const video = getVideo();
        if (current == null || !video || !Number.isFinite(Number(epochSeconds))) return null;
        return video.currentTime + (Number(epochSeconds) - current);
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
