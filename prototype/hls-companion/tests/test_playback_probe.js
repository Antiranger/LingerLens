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

function chatHarness(overrides = {}) {
  let time = 0; const records = [];
  const counters = { poll: 0, got: 0 };
  const gauges = { on: true, tl: false, overlay: 3, anim: 2 };
  const video = { currentTime: 10, paused: false, readyState: 4, playbackRate: 1,
    buffered: { length: 1, start: () => 0, end: () => 20 },
    getVideoPlaybackQuality: () => ({ totalVideoFrames: 100, droppedVideoFrames: 0 }),
    addEventListener() {}, removeEventListener() {},
  };
  const probe = createPlaybackProbe({ video, enabled: () => true, hidden: () => false,
    now: () => time, emit: row => records.push(row),
    counters: () => ({ ...counters }), gauges: () => ({ ...gauges }), ...overrides });
  return { probe, records, counters, gauges, advance: ms => { time += ms; } };
}

test("chat counters are per-window deltas, and the baseline window claims no delta", () => {
  const h = chatHarness();
  h.probe.tick(); h.advance(5200); h.probe.tick();
  const baseline = h.records[0].chat;
  // Gauges are instantaneous, so they are available immediately. Counters are
  // cumulative, so the first window has nothing to subtract and must say so by
  // omitting the key rather than by reporting a confident zero.
  assert.equal(baseline.on, 1); assert.equal(baseline.tl, 0); assert.equal(baseline.overlay, 3);
  assert.equal(baseline.poll, undefined); assert.equal(baseline.got, undefined);
  h.counters.poll = 2; h.counters.got = 37;
  h.advance(5200); h.probe.tick();
  assert.equal(h.records[1].chat.poll, 2);
  assert.equal(h.records[1].chat.got, 37);
  assert.equal(h.records[1].chat.overlay, 3, "gauges stay instantaneous, never summed");
  // A counter that moves backwards is a new session, not a negative window.
  h.counters.poll = 1;
  h.advance(5200); h.probe.tick();
  assert.equal(h.records[2].chat.poll, 1);
});

test("a broken counter or gauge costs the chat section and nothing else", () => {
  const h = chatHarness({ counters: () => { throw new Error("no counters"); } });
  h.probe.tick(); h.advance(5200); h.probe.tick();
  assert.equal(h.records.length, 1, "the window is still recorded");
  assert.equal(h.records[0].chat.on, 1, "gauges still land when counters throw");
  const g = chatHarness({ gauges: () => { throw new Error("no gauges"); } });
  g.probe.tick(); g.advance(5200); g.probe.tick();
  assert.equal(g.records[0].chat.on, undefined, "a throwing gauge provider contributes nothing");
  g.counters.poll = 5;
  g.advance(5200); g.probe.tick();
  assert.equal(g.records[1].chat.poll, 5, "counters still land when gauges throw");
});

test("a fully populated window still fits the log's per-line character budget", () => {
  const h = chatHarness();
  h.probe.tick(); h.advance(5200); h.probe.tick();
  Object.assign(h.counters, { poll: 20, got: 3741, added: 3700, launch: 15, drop: 311,
    reflow: 15, reflowMs: 42, rewrite: 411 });
  h.probe.measure("chatOverlay", () => h.advance(80));
  h.advance(5200); h.probe.tick();
  assert.ok(JSON.stringify(h.records[1]).length < 1000,
    `record was ${JSON.stringify(h.records[1]).length} chars: ${JSON.stringify(h.records[1])}`);
});

test("frame intervals are bucketed, so a stalled window is visible as buckets not just a max", () => {
  const originalRaf = global.requestAnimationFrame;
  let queued = null;
  /* 探针不再自己续帧，只被动观测「别人」的请求。一个永远挂着的 rAF 请求会强制
     Chromium 持续出帧，把窗口钉在显示器刷新率上——观测行为本身就成了持续合成的
     来源，在排查合成通道问题时这是不能接受的干扰。所以这里由测试扮演应用：每收到
     一帧就再请求下一帧，探针只在旁边计时。 */
  const fakeRaf = fn => { queued = fn; return 1; };
  global.requestAnimationFrame = fakeRaf;
  try {
    const h = chatHarness();
    h.probe.tick();
    const request = () => { global.requestAnimationFrame(() => {}); };
    request();
    const fire = at => {
      const fn = queued; queued = null;
      assert.ok(fn, "the app must have a frame queued");
      fn(at);
      request();
    };
    fire(0); fire(16.7); fire(33.4); fire(150);
    h.advance(5200); h.probe.tick();
    // Three intervals: 16.7, 16.7 and 116.6. Only the last one is late, and it
    // is late enough to land in all four buckets.
    assert.deepEqual(h.records[0].jank.raf, [3, 1, 1, 1, 1, 117]);
    h.probe.dispose();
    assert.equal(global.requestAnimationFrame, fakeRaf,
      "disposing must hand requestAnimationFrame back untouched");
  } finally {
    if (originalRaf === undefined) delete global.requestAnimationFrame;
    else global.requestAnimationFrame = originalRaf;
  }
});
