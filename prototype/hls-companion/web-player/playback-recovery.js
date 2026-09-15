/*
 * Small, DOM-free policy for recovering a live player after source stalls or
 * gradual drift. The caller owns the actual video element and applies the
 * returned action; keeping the decision pure makes the safety bands easy to
 * test without starting a browser or a live stream.
 */
(function (global) {
  "use strict";

  const SOURCE_STALL_SECONDS = 5;
  const MIN_DESIRED_DELAY_SECONDS = 3;
  const STABLE_BAND_SECONDS = 2;
  const RATE_BAND_SECONDS = 3;
  const SEEK_BUFFER_SECONDS = 10;
  const RATE_BUFFER_SECONDS = 5;
  const RECOVERY_SEEK_EXCESS_SECONDS = 6;
  const DRIFT_SEEK_EXCESS_SECONDS = 8;
  const RECOVERY_PLAYBACK_RATE = 1.08;

  function finiteOr(value, fallback) {
    if (value === null || value === undefined || value === "") return fallback;
    return Number.isFinite(Number(value)) ? Number(value) : fallback;
  }

  /*
   * Bounded recovery for fatal hls.js errors.
   *
   * The previous handler called `hls.recoverMediaError()` for every fatal
   * MEDIA_ERROR and `hls.startLoad()` for every NETWORK_ERROR, unconditionally.
   * In this bundled hls.js `recoverMediaError()` is a full
   * detachMedia() + attachMedia() + startLoad() rebuild: it discards the whole
   * SourceBuffer, so it always causes a visible rebuffer. With no attempt cap
   * and no backoff, a stream that keeps producing media errors re-enters the
   * rebuild forever -- "video is stuttery" was largely this loop feeding itself.
   *
   * Escalation ladder, reset whenever playback actually makes progress:
   *   media  #1-#2 -> recover-media   (cheapest rebuild)
   *   media  #3    -> swap-codec      (different failure mode, no rebuild)
   *   media  #4+   -> reload          (full manifest reload)
   *   network      -> reload
   *   attempts >= MSE_RECOVERY_MAX_ATTEMPTS within the window -> give-up
   *
   * Returning an action instead of performing it keeps the policy testable
   * without a browser or a live stream.
   */
  const MSE_RECOVERY_MAX_ATTEMPTS = 5;
  const MSE_RECOVERY_BASE_DELAY_MS = 500;
  const MSE_RECOVERY_MAX_DELAY_MS = 8000;

  function mseRecoveryBackoffMs(attempts) {
    const exponent = Math.max(0, finiteOr(attempts, 0));
    return Math.min(MSE_RECOVERY_MAX_DELAY_MS, MSE_RECOVERY_BASE_DELAY_MS * Math.pow(2, exponent));
  }

  function decideMseErrorRecovery({ errorType, attemptsInWindow = 0 } = {}) {
    const attempts = Math.max(0, finiteOr(attemptsInWindow, 0));
    const delayMs = mseRecoveryBackoffMs(attempts);
    if (attempts >= MSE_RECOVERY_MAX_ATTEMPTS) {
      return {
        action: "give-up",
        delayMs: 0,
        attempts,
        reason: `${attempts} consecutive fatal ${errorType || "unknown"} errors; refusing to rebuild again`,
      };
    }
    if (errorType === "network") {
      return { action: "reload", delayMs, attempts, reason: "network error" };
    }
    if (errorType === "media") {
      if (attempts <= 1) return { action: "recover-media", delayMs, attempts, reason: "media error" };
      if (attempts === 2) return { action: "swap-codec", delayMs, attempts, reason: "media error repeated" };
      return { action: "reload", delayMs, attempts, reason: "media error persisted" };
    }
    return { action: "give-up", delayMs: 0, attempts, reason: `unrecoverable error type: ${errorType}` };
  }

  /**
   * Classify why the local live playlist stopped advancing.
   *
   * `sourceStallSeconds` is measured at the publisher (new private HLS
   * segments), while `sourceIngest[].sourceIdleSeconds` is measured at the
   * yt-dlp download legs.  The former can briefly stop while FFmpeg is still
   * receiving bytes, so it must not be presented as an upstream outage.
   */
  function classifySourceHealth({
    state,
    playlistReady = true,
    sourceStallSeconds = 0,
    sourceIngest = [],
  } = {}) {
    const publisherStall = Math.max(0, finiteOr(sourceStallSeconds, 0));
    const mediaLeg = Array.isArray(sourceIngest)
      ? sourceIngest.find((item) => item && item.role === "media")
      : null;
    const mediaIdleValue = mediaLeg ? finiteOr(mediaLeg.sourceIdleSeconds, null) : null;
    if (state !== "running" || !playlistReady) {
      return {
        active: false,
        kind: "none",
        stallSeconds: publisherStall,
        publisherStallSeconds: publisherStall,
        mediaIdleSeconds: mediaIdleValue,
      };
    }

    // If ingest telemetry is available, it is the authority for an upstream
    // outage. A publisher-only stall is a local packaging delay.
    const upstream = mediaIdleValue === null
      ? publisherStall > SOURCE_STALL_SECONDS
      : mediaIdleValue > SOURCE_STALL_SECONDS;
    const packaging = !upstream && publisherStall > SOURCE_STALL_SECONDS;
    return {
      active: upstream || packaging,
      kind: upstream ? "upstream" : packaging ? "packaging" : "none",
      stallSeconds: upstream && mediaIdleValue !== null ? mediaIdleValue : publisherStall,
      publisherStallSeconds: publisherStall,
      mediaIdleSeconds: mediaIdleValue,
    };
  }

  /**
   * Decide how the caller should move a live video toward its local target.
   *
   * `targetDelay` is the user's local-delay target. `hiddenDelay` is the
   * Companion's withheld media, so the browser should sit at target-hidden
   * behind the local public edge. A seek is only proposed when the forward
   * buffer is already large enough to avoid immediately stalling again.
   */
  function decidePlaybackRecovery({
    playerBehind,
    targetDelay = 15,
    hiddenDelay = 0,
    bufferAhead = 0,
    sourceStallSeconds = 0,
    recovered = false,
  } = {}) {
    const desiredDelay = Math.max(
      MIN_DESIRED_DELAY_SECONDS,
      finiteOr(targetDelay, 15) - Math.max(0, finiteOr(hiddenDelay, 0)),
    );
    const behind = finiteOr(playerBehind, null);
    const buffer = Math.max(0, finiteOr(bufferAhead, 0));
    if (behind === null) return { action: "normal", desiredDelay, playbackRate: 1 };
    if (finiteOr(sourceStallSeconds, 0) > SOURCE_STALL_SECONDS) {
      return { action: "hold", desiredDelay, playbackRate: 1 };
    }

    const excess = behind - desiredDelay;
    if (excess <= STABLE_BAND_SECONDS) {
      return { action: "normal", desiredDelay, playbackRate: 1 };
    }
    const seekThreshold = recovered ? RECOVERY_SEEK_EXCESS_SECONDS : DRIFT_SEEK_EXCESS_SECONDS;
    if (excess >= seekThreshold && buffer >= SEEK_BUFFER_SECONDS) {
      return { action: "seek", desiredDelay, playbackRate: 1 };
    }
    if (excess >= RATE_BAND_SECONDS && buffer >= RATE_BUFFER_SECONDS) {
      return { action: "rate", desiredDelay, playbackRate: RECOVERY_PLAYBACK_RATE };
    }
    return { action: "normal", desiredDelay, playbackRate: 1 };
  }

  const exported = { decidePlaybackRecovery, classifySourceHealth, decideMseErrorRecovery, MSE_RECOVERY_MAX_ATTEMPTS };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = exported;
  } else {
    global.decidePlaybackRecovery = decidePlaybackRecovery;
    global.classifySourceHealth = classifySourceHealth;
    global.LingerLensPlaybackRecovery = exported;
  }
})(typeof window !== "undefined" ? window : globalThis);
