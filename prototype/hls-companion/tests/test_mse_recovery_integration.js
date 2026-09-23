const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { playerContext, spy, stubElement } = require("./player-harness.js");

function setup() {
  const browser = {};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web-player/playback-recovery.js"), "utf8"), { window: browser });
  const timers = new Map();
  let serial = 0;
  browser.setTimeout = (fn) => { timers.set(++serial, fn); return serial; };
  browser.clearTimeout = (id) => timers.delete(id);
  class FakeHls {
    static isSupported() { return true; }
    static Events = { MANIFEST_PARSED: "manifest", LEVEL_UPDATED: "level", ERROR: "error" };
    static ErrorTypes = { NETWORK_ERROR: "network", MEDIA_ERROR: "media" };
    constructor() { this.handlers = {}; this.actions = []; }
    on(name, fn) { this.handlers[name] = fn; }
    loadSource() {}
    attachMedia() {}
    recoverMediaError() { this.actions.push("recover"); }
    swapAudioCodec() { this.actions.push("swap"); }
    startLoad() { this.actions.push("load"); }
    fatal(type = "media") { this.handlers.error(null, { fatal: true, type, details: "test" }); }
  }
  browser.Hls = FakeHls;
  const video = stubElement();
  const clock = { now: 0 };
  const h = playerContext({
    state: { hls: null, lastPlaylistUrl: null, latestLevelDetails: null, browserLatency: null,
      mseRecoveryAttempts: 0, mseRecoveryProgressAt: 0, mseRecoveryTimer: null,
      mseRecoveryProgressWall: null, mseRecoveryPlayed: 0 },
    globals: { window: browser, Hls: FakeHls, video, stage: stubElement(), destroyPlayer: spy(),
      performance: { now: () => clock.now },
      attemptAutoplay: spy(), showError: spy(), describeFatal: () => "test" },
    functions: ["attach", "notePlaybackProgress"],
  });
  h.call("attach", "/hls/live.m3u8");
  return { ...h, timers, browser, video, clock, player: h.read().hls,
    fire() { const [id, fn] = timers.entries().next().value; timers.delete(id); fn(); } };
}

test("the browser recovery export reaches the actual fatal-error caller", () => {
  const h = setup();
  assert.doesNotThrow(() => h.player.fatal());
  h.fire();
  assert.deepEqual(h.player.actions, ["recover"]);
});

test("a burst of fatal errors schedules one recovery and spends one attempt", () => {
  const h = setup();
  for (let i = 0; i < 10; i++) h.player.fatal();
  assert.equal(h.timers.size, 1);
  assert.equal(h.read().mseRecoveryAttempts, 1);
});

test("codec swap also rebuilds decoding instead of leaving a stopped loader", () => {
  const h = setup();
  h.write({ mseRecoveryAttempts: 2 });
  h.player.fatal();
  h.fire();
  assert.deepEqual(h.player.actions, ["swap", "recover"]);
});

test("an old player cannot recover a replacement", () => {
  const h = setup();
  h.player.fatal();
  h.write({ hls: null });
  h.fire();
  assert.deepEqual(h.player.actions, []);
});

test("real playback cancels a pending rebuild; sustained playback resets attempts", () => {
  const h = setup();
  Object.assign(h.video, { currentTime: 0, paused: false, seeking: false, readyState: 4, playbackRate: 1 });
  h.call("notePlaybackProgress");
  h.player.fatal();
  for (let i = 1; i <= 2; i++) {
    h.clock.now = i * 250;
    h.video.currentTime = i / 4;
    h.call("notePlaybackProgress");
  }
  assert.equal(h.timers.size, 0);
  assert.equal(h.read().mseRecoveryAttempts, 1, "a short burst must not reset the failure budget");
  for (let i = 3; i <= 8; i++) {
    h.clock.now = i * 250;
    h.video.currentTime = i / 4;
    h.call("notePlaybackProgress");
  }
  assert.equal(h.read().mseRecoveryAttempts, 0);
});

test("a seek jump does not replenish the recovery budget", () => {
  const h = setup();
  Object.assign(h.video, { currentTime: 0, paused: false, seeking: false, readyState: 4 });
  h.call("notePlaybackProgress");
  h.player.fatal();
  h.clock.now = 250;
  h.video.currentTime = 60;
  h.call("notePlaybackProgress");
  assert.equal(h.read().mseRecoveryAttempts, 1);
  assert.equal(h.timers.size, 1);
});
