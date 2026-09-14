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
    sourceStallSeconds: 18,
    recovered: false,
  }), { action: "hold", desiredDelay: 12, playbackRate: 1 });
});

test("seeks to the target edge distance immediately after a recovered stall", () => {
  assert.deepEqual(decidePlaybackRecovery({
    playerBehind: 43,
    targetDelay: 15,
    hiddenDelay: 3,
    bufferAhead: 20,
    sourceStallSeconds: 0,
    recovered: true,
  }), { action: "seek", desiredDelay: 12, playbackRate: 1 });
});

test("uses a small temporary rate increase for a moderate drift", () => {
  assert.deepEqual(decidePlaybackRecovery({
    playerBehind: 17,
    targetDelay: 15,
    hiddenDelay: 3,
    bufferAhead: 20,
    sourceStallSeconds: 0,
    recovered: false,
  }), { action: "rate", desiredDelay: 12, playbackRate: 1.08 });
});

test("does not seek into an insufficient buffer", () => {
  assert.deepEqual(decidePlaybackRecovery({
    playerBehind: 43,
    targetDelay: 15,
    hiddenDelay: 3,
    bufferAhead: 4,
    sourceStallSeconds: 0,
    recovered: true,
  }), { action: "normal", desiredDelay: 12, playbackRate: 1 });
});

test("restores normal speed inside the target band", () => {
  assert.deepEqual(decidePlaybackRecovery({
    playerBehind: 13,
    targetDelay: 15,
    hiddenDelay: 3,
    bufferAhead: 12,
    sourceStallSeconds: 0,
    recovered: false,
  }), { action: "normal", desiredDelay: 12, playbackRate: 1 });
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
