/*
 * A3: the stall diagnostics must state what happened, not what is about to.
 *
 * The quiet-source line was written on the transition into "stalled" and said
 * "未打扰播放" -- and then, in the same call, the banner was shown and playback
 * paused whenever the buffer was under three seconds, which is most of the time a
 * stall is worth reporting at all. A live session on 2026-09-17 wrote
 * "直播源已停 8 秒（upstream），播放缓冲仍有 2 秒，未打扰播放" and paused playback
 * immediately afterwards.
 *
 * So these tests run the SHIPPED updateStallOverlay against a minimal DOM, a
 * controllable buffer, real diagnostics spies and the real classification, and
 * assert on the PAUSE/PLAY CALL SEQUENCE plus the records the function actually
 * emitted -- never on whether a sentence still exists in the source.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { elementLookup, playerContext, spy, stubElement } = require("./player-harness.js");

const policy = require(path.resolve(__dirname, "../web-player/playback-recovery.js"));

const TEST_TIMEOUT_MS = 5000;

function overlay({ paused = false, ahead = 10, suppressed = false, autoPaused = false } = {}) {
  const aheadValue = { value: ahead };
  const video = stubElement();
  video.paused = paused;
  video.pause = spy();
  video.play = spy(async () => {});

  const el = elementLookup();
  const globals = {
    el,
    video,
    setMediaLoading: spy(),
    updateLabel: (key, fallback) => fallback || key,
    diagnosticsBar: { push: spy() },
    flushDevLog: spy(),
    bufferAhead: () => aheadValue.value,
    window: { classifySourceHealth: policy.classifySourceHealth },
  };
  const context = playerContext({
    state: {
      autoPausedForStall: autoPaused,
      stallAutoPauseSuppressed: suppressed,
      autoResumeInFlight: false,
      quietSourceLogged: false,
    },
    globals,
    functions: ["updateStallOverlay", "stallBannerText"],
    constants: ["STALL_VISIBLE_BUFFER_SECONDS", "STALL_RESUME_BUFFER_SECONDS"],
  });
  return {
    ...context,
    video,
    globals,
    banner: el("stallBanner"),
    push: globals.diagnosticsBar.push,
    setAhead: (value) => {
      aheadValue.value = value;
    },
  };
}

function runningData(overrides = {}) {
  return {
    state: "running",
    playlistReady: true,
    sourceStallSeconds: 14,
    sourceIngest: [{ role: "media", sourceIdleSeconds: 13.6 }],
    targetDuration: 6,
    ...overrides,
  };
}

function sourceRecords(push) {
  return push.calls.filter(([, source]) => source === "source");
}

function allText(push) {
  return push.calls.map(([, , message]) => String(message)).join("\n");
}

test(
  "a thin buffer records the pause it really performed, and claims no quiet (D)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = overlay({ paused: false, ahead: 2.9 });
    h.call("updateStallOverlay", runningData());

    assert.equal(h.video.pause.count(), 1, "the automatic pause must still happen");
    assert.equal(h.banner.hidden, false, "the viewer must still see the banner");
    const text = allText(h.push);
    assert.doesNotMatch(text, /未打扰/, "the log claimed it left playback alone");
    assert.match(text, /当前播放缓冲/, "the record must state the buffer it saw");
    const records = sourceRecords(h.push);
    assert.equal(records.length, 2, "one source fact and one action");
    assert.match(String(records[1][2]), /已自动暂停/, "the performed pause must be recorded");
  },
);

test(
  "a deep buffer records the quiet source without touching playback (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    for (const ahead of [3.0, 10.0]) {
      const h = overlay({ paused: false, ahead });
      h.call("updateStallOverlay", runningData());
      assert.equal(h.video.pause.count(), 0, `ahead=${ahead} must not pause`);
      assert.equal(h.banner.hidden, true, `ahead=${ahead} must not show the banner`);
      const records = sourceRecords(h.push);
      assert.equal(records.length, 1, `ahead=${ahead} records the source fact once`);
      assert.match(String(records[0][2]), /当前播放缓冲/);
      assert.doesNotMatch(allText(h.push), /未打扰/);
    }
  },
);

test(
  "one record per quiet-source transition, not one per poll (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = overlay({ paused: false, ahead: 10 });
    h.call("updateStallOverlay", runningData());
    h.call("updateStallOverlay", runningData());
    h.call("updateStallOverlay", runningData());
    assert.equal(sourceRecords(h.push).length, 1, "the transition must be logged once");

    // The source recovers and goes quiet again: that is a new transition.
    h.call("updateStallOverlay", runningData({ sourceStallSeconds: 0.4, sourceIngest: [{ role: "media", sourceIdleSeconds: 0.2 }] }));
    h.call("updateStallOverlay", runningData());
    assert.equal(sourceRecords(h.push).length, 2, "a new transition must be logged again");
  },
);

test(
  "a viewer pause or an explicit suppression is never reported as this round's pause (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const paused = overlay({ paused: true, ahead: 2.9 });
    paused.call("updateStallOverlay", runningData());
    assert.equal(paused.video.pause.count(), 0, "an already paused element must not be paused");
    assert.equal(
      sourceRecords(paused.push).length,
      1,
      "no action may be recorded when no action was taken",
    );

    const suppressed = overlay({ paused: false, ahead: 2.9, suppressed: true });
    suppressed.call("updateStallOverlay", runningData());
    assert.equal(suppressed.video.pause.count(), 0, "the viewer's play must still outrank the pause");
    assert.equal(sourceRecords(suppressed.push).length, 1);
  },
);

test(
  "the automatic resume still fires once the buffer can carry playback (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = overlay({ paused: true, ahead: 9.9, autoPaused: true });
    h.call("updateStallOverlay", runningData());
    assert.equal(h.video.play.count(), 0, "below the resume buffer it must keep waiting");
    assert.equal(h.read().autoPausedForStall, true);
    assert.equal(h.banner.hidden, false);

    h.setAhead(10);
    h.call("updateStallOverlay", runningData());
    assert.equal(h.video.play.count(), 1, "at the resume buffer it must resume once");
    assert.equal(h.read().autoPausedForStall, false);
    assert.equal(h.banner.hidden, true);

    h.call("updateStallOverlay", runningData());
    assert.equal(h.video.play.count(), 1, "and not resume again on the next poll");
  },
);

test(
  "the error path keeps its own message and adds no automatic behaviour (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = overlay({ paused: false, ahead: 2.9, autoPaused: true });
    h.call("updateStallOverlay", { state: "error", error: "FFmpeg exited" });

    assert.equal(h.read().autoPausedForStall, false, "an errored session is not a paused one");
    assert.equal(h.video.pause.count(), 0);
    assert.equal(h.globals.setMediaLoading.count(), 1);
    assert.match(String(h.banner.textContent), /FFmpeg exited/);
    const errors = h.push.calls.filter(([level, source]) => level === "error" && source === "session");
    assert.equal(errors.length, 1, "the session error is recorded once");
    assert.equal(sourceRecords(h.push).length, 0, "no stall record may be invented");
  },
);
