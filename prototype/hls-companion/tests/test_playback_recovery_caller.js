/*
 * E1-R, second half: the REAL caller, not a copy of it.
 *
 * The policy's signature is only half the interface. The other half is what
 * `updatePlaybackRecovery` actually passes it, and a test that re-implements the
 * caller's three lines would keep passing while the shipped caller kept handing
 * over a number. So this file slices the real function out of player.js, wires
 * `window.decidePlaybackRecovery` to a spy that FORWARDS to the real policy, and
 * asserts on both what was passed and what the real policy then decided.
 *
 * The classification is the real `classifySourceHealth`, so the packaging-versus
 * upstream distinction is the shipped one rather than a fixture's opinion.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  playerContext,
  sessionGlobals,
  spy,
  stubElement,
} = require("./player-harness.js");

const policy = require(path.resolve(__dirname, "../web-player/playback-recovery.js"));

const TEST_TIMEOUT_MS = 5000;

function caller({ window: windowOverrides = {}, globals = {} } = {}) {
  const passed = [];
  const video = stubElement();
  const context = playerContext({
    state: {
      sourceWasStalled: false,
      lastRecoverySeekAt: 0,
    },
    globals: {
      ...sessionGlobals(),
      video,
      performance: { now: () => 0 },
      latestLevelDetails: null,
      subtitleScheduler: { retime: spy() },
      renderSubtitle: spy(),
      window: {
        classifySourceHealth: policy.classifySourceHealth,
        decidePlaybackRecovery: (input) => {
          passed.push(input);
          return policy.decidePlaybackRecovery(input);
        },
        ...windowOverrides,
      },
      ...globals,
    },
    functions: ["updatePlaybackRecovery"],
  });
  return { ...context, passed, video };
}

function runningData(overrides = {}) {
  return {
    state: "running",
    playlistReady: true,
    targetDelaySeconds: 15,
    hiddenMediaSeconds: 3,
    targetDuration: 6,
    ...overrides,
  };
}

test(
  "an ingest-confirmed outage is passed as a boolean true and really holds (D)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = caller();
    h.call(
      "updatePlaybackRecovery",
      runningData({
        sourceStallSeconds: 14,
        sourceIngest: [{ role: "media", sourceIdleSeconds: 13.6 }],
      }),
      42,
      2,
    );

    assert.equal(h.passed.length, 1, "the caller must consult the policy");
    assert.equal(h.passed[0].upstreamStalled, true, "13.6s of leg silence is an upstream outage");
    assert.equal(typeof h.passed[0].upstreamStalled, "boolean");
    assert.equal(h.passed[0].sourceStallSeconds, undefined, "the seconds interface is gone");
    assert.equal(h.video.playbackRate, 1, "the real policy must have held catch-up");
  },
);

test(
  "a publisher-only pause is passed as false, so catch-up is not blocked (D)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = caller();
    h.call(
      "updatePlaybackRecovery",
      runningData({
        // The playlist stopped advancing while the ingest legs stayed healthy:
        // a local packaging hiccup, not an outage.
        sourceStallSeconds: 8,
        sourceIngest: [{ role: "media", sourceIdleSeconds: 0.4 }],
      }),
      17,
      20,
    );

    assert.equal(h.passed[0].upstreamStalled, false, "a packaging pause is not an outage");
    assert.equal(typeof h.passed[0].upstreamStalled, "boolean");
    assert.equal(h.video.playbackRate, 1.08, "the real policy must still allow catch-up");
  },
);

test(
  "the fallback classifier also produces a boolean (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = caller({ window: { classifySourceHealth: undefined } });
    h.call(
      "updatePlaybackRecovery",
      runningData({ sourceStallSeconds: 14, sourceIngest: [] }),
      42,
      2,
    );

    assert.equal(
      h.passed[0].upstreamStalled,
      true,
      "with no classification script the publisher stall still is an outage",
    );
    assert.equal(typeof h.passed[0].upstreamStalled, "boolean");
    assert.equal(h.video.playbackRate, 1);
  },
);

test(
  "the ordinary branches are unchanged (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = caller();
    h.call(
      "updatePlaybackRecovery",
      runningData({ sourceStallSeconds: 0.4, sourceIngest: [{ role: "media", sourceIdleSeconds: 0.2 }] }),
      13,
      12,
    );
    assert.equal(h.passed[0].upstreamStalled, false);
    assert.equal(h.video.playbackRate, 1, "inside the target band nothing changes");
  },
);
