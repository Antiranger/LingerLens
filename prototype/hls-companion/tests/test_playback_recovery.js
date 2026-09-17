const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  decidePlaybackRecovery,
  classifySourceHealth,
  decideMseErrorRecovery,
  MSE_RECOVERY_MAX_ATTEMPTS,
} = require(path.resolve(__dirname, "../web-player/playback-recovery.js"));

test("holds normal playback while the source is stalled", () => {
  assert.deepEqual(decidePlaybackRecovery({
    playerBehind: 42,
    targetDelay: 15,
    hiddenDelay: 3,
    bufferAhead: 2,
    upstreamStalled: true,
    recovered: false,
  }), { action: "hold", desiredDelay: 12, playbackRate: 1 });
});

test("seeks to the target edge distance immediately after a recovered stall", () => {
  assert.deepEqual(decidePlaybackRecovery({
    playerBehind: 43,
    targetDelay: 15,
    hiddenDelay: 3,
    bufferAhead: 20,
    upstreamStalled: false,
    recovered: true,
  }), { action: "seek", desiredDelay: 12, playbackRate: 1 });
});

test("uses a small temporary rate increase for a moderate drift", () => {
  assert.deepEqual(decidePlaybackRecovery({
    playerBehind: 17,
    targetDelay: 15,
    hiddenDelay: 3,
    bufferAhead: 20,
    upstreamStalled: false,
    recovered: false,
  }), { action: "rate", desiredDelay: 12, playbackRate: 1.08 });
});

test("does not seek into an insufficient buffer", () => {
  assert.deepEqual(decidePlaybackRecovery({
    playerBehind: 43,
    targetDelay: 15,
    hiddenDelay: 3,
    bufferAhead: 4,
    upstreamStalled: false,
    recovered: true,
  }), { action: "normal", desiredDelay: 12, playbackRate: 1 });
});

test("restores normal speed inside the target band", () => {
  assert.deepEqual(decidePlaybackRecovery({
    playerBehind: 13,
    targetDelay: 15,
    hiddenDelay: 3,
    bufferAhead: 12,
    upstreamStalled: false,
    recovered: false,
  }), { action: "normal", desiredDelay: 12, playbackRate: 1 });
});

/*
 * E1-R: the recovery policy consumes a boolean, it does not re-classify the source.
 *
 * The caller passes `classifySourceHealth`'s own answer. Judging a number again
 * against a flat 5s was a second threshold, on a different ruler from the one
 * that produced it, so the interface is now the answer itself: `true` for an
 * ingest-confirmed upstream outage, `false` for everything else.
 */
function recoveryActionFor(healthInput) {
  const health = classifySourceHealth(healthInput);
  const isStalled = health.active === true && health.kind === "upstream";
  return decidePlaybackRecovery({
    playerBehind: 42,
    targetDelay: 15,
    hiddenDelay: 3,
    bufferAhead: 2,
    upstreamStalled: isStalled,
    recovered: false,
  }).action;
}

test("the boolean holds catch-up, and a raw stall count does not stand in for it", () => {
  // D: an upstream stall holds, whatever the classification measured.
  assert.equal(decidePlaybackRecovery({
    playerBehind: 42,
    targetDelay: 15,
    hiddenDelay: 3,
    bufferAhead: 2,
    upstreamStalled: true,
    recovered: false,
  }).action, "hold");

  // D: 3 seconds used to hold on its own (`> 0`), and it no longer means
  // anything here -- the number is not the interface. With a 2s buffer neither
  // catch-up branch qualifies, so the answer is normal.
  assert.equal(decidePlaybackRecovery({
    playerBehind: 42,
    targetDelay: 15,
    hiddenDelay: 3,
    bufferAhead: 2,
    sourceStallSeconds: 3,
    recovered: false,
  }).action, "normal");
});

test("only a strict true holds, so a non-boolean cannot pose as a stall", () => {
  const base = { playerBehind: 42, targetDelay: 15, hiddenDelay: 3, bufferAhead: 2 };
  // G: omitting it means "not stalled", not "unknown".
  assert.equal(decidePlaybackRecovery({ ...base }).action, "normal");
  // G: a truthy-but-wrong value must not silently hold catch-up.
  assert.equal(decidePlaybackRecovery({ ...base, upstreamStalled: "true" }).action, "normal");
  assert.equal(decidePlaybackRecovery({ ...base, upstreamStalled: 1 }).action, "normal");
  assert.equal(decidePlaybackRecovery({ ...base, upstreamStalled: 1.08 }).action, "normal");
  assert.equal(decidePlaybackRecovery({ ...base, upstreamStalled: true }).action, "hold");
  assert.equal(decidePlaybackRecovery({ ...base, upstreamStalled: false }).action, "normal");
});

test("the recovery action follows the scaled threshold, not a flat one", () => {
  const base = { state: "running", playlistReady: true, sourceStallSeconds: 0.2 };
  const media = (idle) => [{ role: "media", sourceIdleSeconds: idle }];
  // Short segments keep the 5s floor, so 6s of leg silence is an outage.
  assert.equal(recoveryActionFor({ ...base, targetDuration: 1, sourceIngest: media(6) }), "hold");
  // Longer segments move the threshold with the segment length: the same 6s is
  // inside the normal cadence ...
  assert.equal(recoveryActionFor({ ...base, targetDuration: 6, sourceIngest: media(6) }), "normal");
  // ... and just past it, it is an outage again.
  assert.equal(recoveryActionFor({ ...base, targetDuration: 6, sourceIngest: media(8.1) }), "hold");
  // Unknown segment length keeps the 5s floor, on both sides of it.
  assert.equal(recoveryActionFor({ ...base, sourceIngest: media(5.1) }), "hold");
  assert.equal(recoveryActionFor({ ...base, sourceIngest: media(4.9) }), "normal");
});

test("does not call a publisher-only playlist pause an upstream outage", () => {
  assert.deepEqual(classifySourceHealth({
    state: "running",
    playlistReady: true,
    sourceStallSeconds: 8,
    sourceIngest: [{ role: "media", sourceIdleSeconds: 0.4 }],
  }), {
    active: true,
    kind: "packaging",
    stallSeconds: 8,
    publisherStallSeconds: 8,
    mediaIdleSeconds: 0.4,
  });
});

test("keeps a real media download outage visible", () => {
  assert.deepEqual(classifySourceHealth({
    state: "running",
    playlistReady: true,
    sourceStallSeconds: 14,
    sourceIngest: [{ role: "media", sourceIdleSeconds: 13.6 }],
  }), {
    active: true,
    kind: "upstream",
    stallSeconds: 13.6,
    publisherStallSeconds: 14,
    mediaIdleSeconds: 13.6,
  });
});

/*
 * Live 2026-09-16 (1080p60): segments were 5.005s and 1.001s, against a flat 5s
 * publisher threshold -- five milliseconds of margin. The banner fired on 8 of
 * 101 samples of a stream that was provably healthy (privateMediaSeconds 4609s
 * over 4606s of uptime, zero backlog), showing the user the segment length
 * itself as "已停 5 秒". `sourceStallSeconds` is "time since a NEW segment", so
 * its normal peak IS the segment length and the threshold has to clear it.
 */
test("a healthy 5s-segment stream does not report a stall at its own cadence", () => {
  const healthy = classifySourceHealth({
    state: "running",
    playlistReady: true,
    sourceStallSeconds: 5.7,          // worst normal peak measured over 500s
    sourceIngest: [{ role: "media", sourceIdleSeconds: 0.4 }],
    targetDuration: 6,                // publisher's ceil(5.005)
  });
  assert.equal(healthy.active, false);
  assert.equal(healthy.kind, "none");
});

test("a publisher stall beyond the segment length still reports", () => {
  assert.deepEqual(classifySourceHealth({
    state: "running",
    playlistReady: true,
    sourceStallSeconds: 14,
    sourceIngest: [{ role: "media", sourceIdleSeconds: 0.4 }],
    targetDuration: 6,
  }), {
    active: true,
    kind: "packaging",
    stallSeconds: 14,
    publisherStallSeconds: 14,
    mediaIdleSeconds: 0.4,
  });
});

test("the ingest legs are judged on the same segment-scaled threshold", () => {
  // Measured live over 600s: the media leg's byte idle reached 5.6s and the
  // per-pump byte gap 5.7s on a healthy stream whose segments are 5.005s, so a
  // flat 5s threshold sat inside the normal peak. Both signals are bounded by
  // the segment cadence -- the publisher emits one segment per cadence, a
  // download leg receives one segment's bytes per cadence.
  const normal = classifySourceHealth({
    state: "running",
    playlistReady: true,
    sourceStallSeconds: 0.2,
    sourceIngest: [{ role: "media", sourceIdleSeconds: 5.6 }],
    targetDuration: 6,
  });
  assert.equal(normal.active, false);

  const outage = classifySourceHealth({
    state: "running",
    playlistReady: true,
    sourceStallSeconds: 0.2,
    sourceIngest: [{ role: "media", sourceIdleSeconds: 20 }],
    targetDuration: 6,
  });
  assert.equal(outage.active, true);
  assert.equal(outage.kind, "upstream");
});

test("a short-segment stream keeps the original 5s threshold on both sides", () => {
  // 1s segments must not become MORE sensitive than they were before: the
  // scaled threshold is a floor, not a replacement.
  const stalled = classifySourceHealth({
    state: "running",
    playlistReady: true,
    sourceStallSeconds: 6,
    sourceIngest: [{ role: "media", sourceIdleSeconds: 6 }],
    targetDuration: 1,
  });
  assert.equal(stalled.active, true);
  // Upstream wins when the legs are the ones that went quiet.
  assert.equal(stalled.kind, "upstream");
});

test("no targetDuration keeps the previous behaviour exactly", () => {
  const unknown = classifySourceHealth({
    state: "running",
    playlistReady: true,
    sourceStallSeconds: 8,
    sourceIngest: [{ role: "media", sourceIdleSeconds: 0.4 }],
  });
  assert.equal(unknown.active, true);
  assert.equal(unknown.kind, "packaging");
});

// Regression: the hls.js ERROR handler used to call recoverMediaError() for
// every fatal MEDIA_ERROR and startLoad() for every NETWORK_ERROR with no
// attempt cap and no backoff. In the bundled hls.js recoverMediaError() is a
// full detachMedia()+attachMedia()+startLoad() rebuild, so it always costs a
// visible rebuffer -- and a stream that keeps producing media errors re-entered
// the rebuild forever, which reads to a viewer as "the video keeps stuttering".
test("fatal media errors escalate and then give up instead of rebuilding forever", () => {
  assert.equal(decideMseErrorRecovery({ errorType: "media", attemptsInWindow: 0 }).action, "recover-media");
  assert.equal(decideMseErrorRecovery({ errorType: "media", attemptsInWindow: 1 }).action, "recover-media");
  assert.equal(decideMseErrorRecovery({ errorType: "media", attemptsInWindow: 2 }).action, "swap-codec");
  assert.equal(decideMseErrorRecovery({ errorType: "media", attemptsInWindow: 3 }).action, "reload");
  const exhausted = decideMseErrorRecovery({ errorType: "media", attemptsInWindow: MSE_RECOVERY_MAX_ATTEMPTS });
  assert.equal(exhausted.action, "give-up");
  assert.ok(exhausted.reason.length > 0, "giving up must explain itself to the viewer");
});

test("every recovery action waits before rebuilding, with growing backoff", () => {
  const first = decideMseErrorRecovery({ errorType: "media", attemptsInWindow: 0 });
  const second = decideMseErrorRecovery({ errorType: "media", attemptsInWindow: 2 });
  assert.ok(first.delayMs > 0, "an immediate rebuild re-enters the same failure");
  assert.ok(second.delayMs > first.delayMs, "backoff must grow");
  assert.equal(decideMseErrorRecovery({ errorType: "network", attemptsInWindow: 0 }).action, "reload");
  assert.equal(
    decideMseErrorRecovery({ errorType: "network", attemptsInWindow: MSE_RECOVERY_MAX_ATTEMPTS }).action,
    "give-up"
  );
});

test("an unknown fatal error type is never retried", () => {
  const decision = decideMseErrorRecovery({ errorType: null, attemptsInWindow: 0 });
  assert.equal(decision.action, "give-up");
  assert.equal(decision.delayMs, 0);
});
