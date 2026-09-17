/*
 * Small, DOM-free policy for recovering a live player after source stalls or
 * gradual drift. The caller owns the actual video element and applies the
 * returned action; keeping the decision pure makes the safety bands easy to
 * test without starting a browser or a live stream.
 */
(function (global) {
  "use strict";

  const SOURCE_STALL_SECONDS = 5;
  /*
   * Margin above the longest segment, for the publisher-side stall check.
   *
   * `sourceStallSeconds` is "seconds since a NEW segment appeared", so on a
   * perfectly healthy stream it ramps from 0 to about one segment and resets --
   * the counter's normal peak IS the segment length. A threshold that does not
   * clear that peak therefore measures segment-publication jitter rather than a
   * stall.
   *
   * Measured live 2026-09-16 (1080p60): segments were 5.005s (26% of them) and
   * 1.001s (71%), against this 5s threshold -- five milliseconds of margin. The
   * banner fired on 8 of 101 samples of a stream that was provably healthy
   * (privateMediaSeconds 4609s over 4606s of uptime, zero backlog, zero dropped
   * cues), and the number it showed the user ("已停 5 秒") was just the segment
   * length. Worst normal peak over that run: 5.7s.
   *
   * Two seconds clears that worst case with room to spare.
   *
   * Nothing downstream restarts a stalled leg. ``RecoveryPolicy`` classifies the
   * stall (warning / reconnecting / failed) and its ``action`` field has no
   * consumer anywhere in the repo, so these thresholds are not racing a recovery
   * that begins at 8s -- a stalled source stays stalled until the viewer acts.
   */
  const STALL_MARGIN_SECONDS = 2;
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
   * Seconds without a new segment, or without a delivered byte, before the
   * source counts as stalled.
   *
   * Scaled to the stream: a 1s-segment stream keeps the original 5s, a
   * 5s-segment stream gets 8s. With no value at hand the original constant
   * stands, so a caller that has no status yet behaves exactly as before.
   */
  function publisherStallThreshold(targetDuration) {
    const longest = Math.ceil(finiteOr(targetDuration, 0));
    if (!(longest > 0)) return SOURCE_STALL_SECONDS;
    return Math.max(SOURCE_STALL_SECONDS, longest + STALL_MARGIN_SECONDS);
  }

  /**
   * Classify why the local live playlist stopped advancing.
   *
   * `sourceStallSeconds` is measured at the publisher (new private HLS
   * segments), while `sourceIngest[].sourceIdleSeconds` is measured at the
   * yt-dlp download legs -- from the MEDIA bytes they relay, never from their
   * log output. That distinction is load-bearing: a download that has stalled
   * but keeps printing retry lines every 0.5s is not a healthy source, and a
   * log-line clock cannot tell the difference.
   *
   * Both are judged against the same segment-scaled threshold, for the same
   * reason: each is bounded by the segment cadence. The publisher emits one
   * segment per cadence; a download leg receives one segment's bytes per
   * cadence and is otherwise silent. Measured on a healthy 1080p60 stream over
   * 600s (1481 samples, 5.005s segments): media leg idle p50 2.8s / max 5.6s,
   * per-pump byte gap p50 5.2s / max 5.7s -- the normal peak IS the segment
   * length on both sides. A flat 5s threshold sat inside that peak, which read
   * as a network outage while the stream was fine.
   *
   * Upstream still wins over packaging: when the download legs are the ones
   * that went quiet, that is the more actionable diagnosis of the two.
   */
  function classifySourceHealth({
    state,
    playlistReady = true,
    sourceStallSeconds = 0,
    sourceIngest = [],
    targetDuration = 0,
  } = {}) {
    const publisherStall = Math.max(0, finiteOr(sourceStallSeconds, 0));
    const publisherThreshold = publisherStallThreshold(targetDuration);
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
    //
    // BOTH sides are judged against the segment-scaled threshold, because both
    // signals are bounded by the segment cadence: the publisher *emits* one
    // segment per cadence, and each ingest leg *receives* one segment's bytes
    // per cadence. Measured on a healthy 1080p60 stream over 600s (1481
    // samples), with segments at 5.005s:
    //     media leg idle      p50 2.8s  p95 5.1s  max 5.6s
    //     per-pump byte gap   p50 5.2s  p95 5.6s  max 5.7s
    // i.e. the normal peak IS the segment length on this side too. A flat 5s
    // threshold sat inside that peak, which is the false "upstream outage"
    // that a log-line clock used to hide.
    const upstream = mediaIdleValue === null
      ? publisherStall > publisherThreshold
      : mediaIdleValue > publisherThreshold;
    const packaging = !upstream && publisherStall > publisherThreshold;
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
    /*
     * `sourceStallSeconds` here is the CLASSIFICATION's own output, not a raw
     * measurement: the caller passes 0 unless `classifySourceHealth` reported
     * `active && kind === "upstream"`, and otherwise passes the stall it
     * measured. So any positive value already means "the source is stalled".
     *
     * This used to re-test that value against a flat 5s -- a second
     * classification on a different ruler from the one that produced it. The
     * scaled threshold is `max(5, ceil(targetDuration) + 2)`, so the value that
     * arrives is always above 5 and the flat test could never be false. It read
     * like a safety check and was not one; two rules for one question is how
     * they drift apart.
     */
    if (finiteOr(sourceStallSeconds, 0) > 0) {
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
