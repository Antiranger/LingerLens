const test = require("node:test");
const assert = require("node:assert/strict");
const { createPlaybackProbe } = require("../web-player/diagnostics-log.js");
const { playerContext } = require("./player-harness.js");

test("unchanged player controls do not repeatedly replace text or accessibility labels", () => {
  let writes = 0;
  const elements = new Map();
  const el = id => {
    if (!elements.has(id)) {
      let text = ""; const attrs = new Map();
      elements.set(id, { dataset: {}, matches: () => false,
        get textContent() { return text; }, set textContent(value) { writes++; text = value; },
        getAttribute: name => attrs.get(name),
        setAttribute: (name, value) => { writes++; attrs.set(name, value); },
      });
    }
    return elements.get(id);
  };
  const video = { paused: false, muted: false, currentTime: 10 };
  const h = playerContext({ functions: ["updatePlayerControls"], globals: { el, video,
    // The control labels resolve through i18n in the page; here the Chinese the
    // call carries as its fallback is the whole contract.
    updateLabel: (key, fallback) => fallback,
    stage: { classList: { toggle() {} } },
    mediaClock: { seekableWallClockRange: () => ({ currentWallTime: 10, startWallTime: 0,
      endWallTime: 20, startPosition: 0, endPosition: 20 }), formatTime: String },
  } });
  h.call("updatePlayerControls"); const initial = writes;
  for (let i = 0; i < 20; i++) h.call("updatePlayerControls");
  assert.equal(writes, initial, "identical updates must not notify the window repeatedly");
  video.paused = true; h.call("updatePlayerControls");
  assert.equal(el("playPause").getAttribute("aria-label"), "播放");
});

function harness() {
  let time = 0, enabled = true, hidden = false;
  const listeners = new Map(), records = [];
  const q = { totalVideoFrames: 100, droppedVideoFrames: 2 };
  const video = { currentTime: 10, paused: false, readyState: 4, playbackRate: 1,
    buffered: { length: 2, start: i => [0, 9][i], end: i => [5, 20][i] },
    getVideoPlaybackQuality: () => ({ ...q }),
    addEventListener: (name, fn) => listeners.set(name, fn),
    removeEventListener: name => listeners.delete(name),
  };
  const probe = createPlaybackProbe({ video, enabled: () => enabled, hidden: () => hidden,
    now: () => time, emit: row => records.push(row) });
  return { probe, video, q, records, listeners,
    advance: ms => { time += ms; }, enable: value => { enabled = value; },
    hide: value => { hidden = value; } };
}

test("probe distinguishes thin buffer, dropped frames, timer blockage and component cost", () => {
  const h = harness(); h.probe.tick();
  h.probe.measure("chatOverlay", () => h.advance(80));
  h.listeners.get("waiting")();
  h.video.currentTime = 19; h.q.totalVideoFrames += 300; h.q.droppedVideoFrames += 9;
  h.advance(5200); h.probe.tick();
  assert.equal(h.records.length, 1);
  const r = h.records[0];
  assert.equal(r.ahead, 1); assert.equal(r.mediaDelta, 9);
  assert.deepEqual(r.frames, [300, 9]); assert.equal(r.events.waiting, 1);
  assert.deepEqual(r.workMs.chatOverlay, [1, 80, 80]);
  assert.equal(r.timerLateMs, 5180);
  assert.ok(JSON.stringify(r).length < 1000);
  h.advance(5000); h.probe.tick();
  assert.deepEqual(h.records[1].events, {});
  assert.deepEqual(h.records[1].workMs, {});
});

test("disabled probe does not sample and preserves return values and exceptions", () => {
  const h = harness(); h.enable(false);
  h.video.getVideoPlaybackQuality = () => { throw Error("must not sample"); };
  assert.equal(h.probe.measure("subtitle", () => 42), 42);
  assert.throws(() => h.probe.measure("subtitle", () => { throw Error("original"); }), /original/);
  h.advance(6000); h.probe.tick(); assert.equal(h.records.length, 0);
  h.probe.dispose(); assert.equal(h.listeners.size, 0);
});

test("hidden transitions and frame-counter reset do not manufacture huge stalls or negative drops", () => {
  const h = harness(); h.probe.tick();
  h.hide(true); h.advance(30000); h.probe.tick();
  h.hide(false); h.advance(30000); h.probe.tick();
  assert.equal(h.records.length, 0);
  h.q.totalVideoFrames = 0; h.q.droppedVideoFrames = 0;
  for (let i = 0; i < 50; i++) { h.advance(100); h.probe.tick(); }
  assert.equal(h.records[0].timerLateMs, 0); assert.equal(h.records[0].frames, null);
});
