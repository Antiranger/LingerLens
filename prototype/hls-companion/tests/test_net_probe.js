"use strict";

/*
 * 渲染进程的请求画像。存在的理由只有一个：每个请求都要过主进程的
 * `protocol.handle('lingerlens')` 代理，而那条路径是原生的流泵——读代码看不出
 * 它被调用了多少次、泵了多少字节。所以这里测的不是"函数返回值对不对"，而是
 * "它数的东西是不是代理看到的那一份"。
 */

const test = require("node:test");
const assert = require("node:assert/strict");

const { createNetProbe, createPlaybackProbe } = require("../web-player/diagnostics-log.js");

function fetchTarget(reply) {
  return {
    fetch: async (url) => {
      if (reply === "throw") throw new Error("network down");
      return { ok: reply !== "bad", headers: { get: () => "4096" }, url };
    },
  };
}

test("requests are tallied per normalized path and the window resets when it is read", async () => {
  const target = fetchTarget();
  const probe = createNetProbe({ target });
  await target.fetch("lingerlens://app/api/live-messages?since=12");
  await target.fetch("lingerlens://app/api/live-messages?since=13");
  await target.fetch("lingerlens://app/media/seg/1001.ts");
  await target.fetch("lingerlens://app/media/seg/1002.ts");
  const window = probe.takeWindow();
  assert.equal(window.req, 4);
  assert.equal(window.paths, 2, "two segments collapse into one path, and the query is dropped");
  assert.equal(window.kb, 16, "four responses of 4096 bytes");
  assert.equal(window.fail, 0);
  const next = probe.takeWindow();
  assert.equal(next.req, 0, "reading a window starts a new one rather than repeating the old one");
  assert.equal(next.kb, 0);
});

test("a rejected request is counted as a failure, keeps its path, and still rejects", async () => {
  const target = fetchTarget("throw");
  const probe = createNetProbe({ target });
  await assert.rejects(() => target.fetch("lingerlens://app/api/status"), /network down/);
  const window = probe.takeWindow();
  assert.equal(window.req, 1);
  assert.equal(window.fail, 1);
  assert.equal(window.top[0][0], "/api/status", "a failure must not vanish from the picture");
});

test("a non-ok response counts as a failure even though it resolved", async () => {
  const target = fetchTarget("bad");
  const probe = createNetProbe({ target });
  await target.fetch("lingerlens://app/api/probe");
  assert.equal(probe.takeWindow().fail, 1);
});

test("XMLHttpRequest is tallied too, because the HLS loader may not use fetch", async () => {
  class FakeXhr {
    constructor() { this.status = 200; this.listeners = {}; }
    open() {}
    send() { for (const fn of this.listeners.loadend || []) fn(); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    getResponseHeader() { return "2048"; }
  }
  const target = { fetch: undefined, XMLHttpRequest: FakeXhr };
  const probe = createNetProbe({ target });
  const request = new target.XMLHttpRequest();
  request.open("GET", "lingerlens://app/api/subtitles?seq=5");
  request.send();
  const window = probe.takeWindow();
  assert.equal(window.req, 1);
  assert.equal(window.kb, 2);
  assert.equal(window.top[0][0], "/api/subtitles");
});

test("paths stay short enough to fit the log line, whatever the URL looks like", () => {
  const probe = createNetProbe({ target: fetchTarget() });
  const label = probe.labelFor("lingerlens://app/media/segment-9f8e7d6c5b4a3210-very-long-name-here.ts");
  assert.ok(label.length <= 26, `label was ${label.length} chars: ${label}`);
  assert.ok(!label.includes("9f8e7d"), "hex ids are folded away, so two segments share one row");
});

test("the playback record carries a net section only when a probe supplies one", async () => {
  let now = 0;
  const rows = [];
  const video = { currentTime: 10, paused: false, readyState: 4, playbackRate: 1,
    buffered: { length: 1, start: () => 0, end: () => 20 },
    getVideoPlaybackQuality: () => ({ totalVideoFrames: 100, droppedVideoFrames: 0 }),
    addEventListener() {}, removeEventListener() {} };
  const target = fetchTarget();
  const net = createNetProbe({ target });
  const probe = createPlaybackProbe({ video, enabled: () => true, hidden: () => false,
    now: () => now, emit: row => rows.push(row), net });
  probe.tick();
  await target.fetch("lingerlens://app/api/live-messages");
  now += 5200;
  probe.tick();
  assert.deepEqual(rows[0].net.top, [["/api/live-messages", 1, 4, rows[0].net.top[0][3], rows[0].net.top[0][4]]]);
  assert.equal(rows[0].net.req, 1);
  assert.ok(JSON.stringify(rows[0]).length < 1000,
    `record was ${JSON.stringify(rows[0]).length} chars`);

  const bare = [];
  const plain = createPlaybackProbe({ video, enabled: () => true, hidden: () => false,
    now: () => now, emit: row => bare.push(row) });
  plain.tick(); now += 5200; plain.tick();
  assert.equal(bare[0].net, undefined, "no probe, no section");
});
