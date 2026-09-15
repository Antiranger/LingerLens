const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  createDiagnosticsLog,
  createDiagnosticsClient,
  createDiagnosticsBar,
  createUpdateClient,
  formatDiagnostics,
  hintFor,
} = require(path.resolve(__dirname, "../web-player/diagnostics-log.js"));

/* ── 最小但诚实的 DOM 替身：元素真的存值、真的派发事件 ── */

function createElement(id) {
  const listeners = {};
  const attributes = {};
  return {
    id,
    hidden: false,
    textContent: "",
    innerHTML: "",
    title: "",
    dataset: {},
    scrollTop: 0,
    scrollHeight: 420,
    addEventListener(name, callback) { (listeners[name] = listeners[name] || []).push(callback); },
    dispatch(name, event = {}) { for (const callback of listeners[name] || []) callback(event); },
    setAttribute: (name, value) => { attributes[name] = String(value); },
    getAttribute: (name) => (name in attributes ? attributes[name] : null),
  };
}

function createDocument(ids) {
  const nodes = new Map(ids.map((id) => [id, createElement(id)]));
  return {
    getElementById: (id) => nodes.get(id) || null,
    node: (id) => nodes.get(id),
    ids,
  };
}

const BAR_IDS = [
  "diagnosticsBar", "diagToggle", "diagBody", "diagList", "diagSummary",
  "diagCount", "diagHint", "diagEmpty", "diagCopy", "diagClear",
];

function createBar(overrides = {}) {
  const log = overrides.log || createDiagnosticsLog();
  const doc = createDocument(BAR_IDS);
  const copied = [];
  const bar = createDiagnosticsBar({
    document: doc,
    log,
    root: doc.node("diagnosticsBar"),
    onCopy: () => copied.push(true),
  });
  return { bar, log, doc, copied };
}

/* ── 记录缓冲区 ── */

test("records carry an increasing id, the given level and a clamped single-line message", () => {
  const log = createDiagnosticsLog({ now: () => 100 });
  log.record("error", "media", "line one\nline two");
  log.record("warn", "request", "   ");
  log.record("info", "app", "x".repeat(500));
  const rows = log.list();
  assert.equal(rows.length, 2, "whitespace-only messages are not records");
  assert.equal(rows[0].id, "c1");
  assert.equal(rows[1].id, "c2");
  assert.equal(rows[0].message, "line one line two");
  assert.equal(rows[1].message.length, 400);
  assert.ok(rows[1].message.endsWith("…"));
});

test("an unknown level degrades to info instead of entering the record verbatim", () => {
  const log = createDiagnosticsLog({ now: () => 1 });
  log.record("catastrophe", "app", "boom");
  assert.equal(log.list()[0].level, "info");
});

test("the ring evicts oldest first once it is full", () => {
  let clock = 0;
  const log = createDiagnosticsLog({ capacity: 3, now: () => (clock += 1) });
  for (const text of ["a", "b", "c", "d"]) log.record("info", "app", text);
  assert.deepEqual(log.list().map((entry) => entry.message), ["b", "c", "d"]);
  assert.equal(log.size, 3);
});

test("an identical message inside the dedupe window is dropped, and outside it is kept", () => {
  let clock = 0;
  const log = createDiagnosticsLog({ now: () => clock });
  assert.ok(log.record("error", "ui", "后台响应超时"));
  clock = 3;
  assert.equal(log.record("error", "ui", "后台响应超时"), null, "within 5s is the same event");
  clock = 9;
  assert.ok(log.record("error", "ui", "后台响应超时"), "outside 5s is a new event");
  assert.equal(log.size, 2);
});

test("backend records merge by seq and never double-count across polls", () => {
  const log = createDiagnosticsLog({ now: () => 50 });
  const batch = [
    { seq: 1, t: 10, level: "info", source: "media", message: "FFmpeg started" },
    { seq: 2, t: 11, level: "error", source: "media", message: "FFmpeg exited with code 1" },
  ];
  assert.equal(log.merge(batch).length, 2);
  assert.equal(log.merge(batch).length, 0, "the same seq must not be merged twice");
  assert.equal(log.merge([{ seq: 3, t: 12, level: "warn", source: "request", message: "bad body" }]).length, 1);
  assert.equal(log.merge([{ level: "info", message: "no seq" }, null, "junk"]).length, 0);
});

test("client and backend records share one timeline ordered by time", () => {
  const log = createDiagnosticsLog({ now: () => 30 });
  log.merge([{ seq: 1, t: 10, level: "info", source: "media", message: "started" }]);
  log.record("error", "ui", "local failure");
  log.merge([{ seq: 2, t: 20, level: "warn", source: "request", message: "later" }]);
  assert.deepEqual(log.list().map((entry) => entry.message), ["started", "later", "local failure"]);
});

test("counts and worstLevel summarise the whole buffer", () => {
  const log = createDiagnosticsLog({ now: () => 1 });
  assert.deepEqual(log.counts(), { info: 0, warn: 0, error: 0, total: 0 });
  assert.equal(log.worstLevel(), "info", "an empty log is not an error state");
  log.record("info", "app", "a");
  log.record("warn", "app", "b");
  assert.equal(log.worstLevel(), "warn");
  log.record("error", "app", "c");
  assert.deepEqual(log.counts(), { info: 1, warn: 1, error: 1, total: 3 });
  assert.equal(log.worstLevel(), "error");
  assert.equal(log.newest("warn").message, "b");
  assert.equal(log.newest().message, "c");
});

test("clear forgets records, ids and merged seqs together", () => {
  const log = createDiagnosticsLog({ now: () => 7 });
  log.record("error", "ui", "boom");
  log.merge([{ seq: 4, t: 7, level: "info", source: "media", message: "merged" }]);
  log.clear();
  assert.equal(log.size, 0);
  assert.equal(log.record("info", "ui", "after clear").id, "c1");
  assert.equal(log.merge([{ seq: 4, t: 8, level: "info", source: "media", message: "merged again" }]).length, 1,
    "a cleared log must accept seqs it saw before");
});

/* ── 后端轮询 ── */

test("the client polls with afterSeq and advances it to maxSeq", async () => {
  const seen = [];
  const client = createDiagnosticsClient({
    fetch: async (url) => {
      seen.push(url);
      return { ok: true, json: async () => ({ records: [], maxSeq: 12, dropped: 0, sessionId: "aa" }) };
    },
  });
  assert.equal(seen.length, 0, "construction must not poll");
  await client.poll();
  await client.poll();
  assert.deepEqual(seen, ["/api/logs?afterSeq=0", "/api/logs?afterSeq=12"]);
});

test("a failed poll reports ok:false and does not advance afterSeq", async () => {
  const seen = [];
  let fail = true;
  const updates = [];
  const client = createDiagnosticsClient({
    onUpdate: (data) => updates.push(data),
    fetch: async (url) => {
      seen.push(url);
      if (fail) throw new Error("Failed to fetch");
      return { ok: true, json: async () => ({ records: [], maxSeq: 3, sessionId: "aa" }) };
    },
  });
  const first = await client.poll();
  assert.equal(first.ok, false);
  assert.match(first.error, /Failed to fetch/);
  assert.equal(updates.at(-1).ok, false);
  fail = false;
  await client.poll();
  assert.deepEqual(seen, ["/api/logs?afterSeq=0", "/api/logs?afterSeq=0"],
    "a failed poll must retry the same window rather than skip it");
});

test("an HTTP error status is a failure, not an empty success", async () => {
  const client = createDiagnosticsClient({ fetch: async () => ({ ok: false, status: 503 }) });
  const result = await client.poll();
  assert.equal(result.ok, false);
  assert.match(result.error, /503/);
});

test("a backend restart rewinds afterSeq to zero", async () => {
  const seen = [];
  const client = createDiagnosticsClient({
    fetch: async (url) => {
      seen.push(url);
      return { ok: true, json: async () => ({ records: [], maxSeq: 40, sessionId: "aa" }) };
    },
  });
  await client.poll();
  assert.equal(client.getAfterSeq(), 40);
  client.resetForRestart();
  await client.poll();
  assert.equal(client.getRestartCount(), 1);
  assert.deepEqual(seen, ["/api/logs?afterSeq=0", "/api/logs?afterSeq=0"]);
});

test("a mid-session seq gap is reported as missed lines, and a first load is not", async () => {
  let body = { records: [{ seq: 4, t: 1, level: "info", source: "media", message: "a" }], maxSeq: 4, sessionId: "aa" };
  const client = createDiagnosticsClient({ fetch: async () => ({ ok: true, json: async () => body }) });
  /* First poll: the page simply opened late. Not a loss during this run. */
  const first = await client.poll();
  assert.equal(first.missed, 0);
  /* The ring wrapped while the tab was throttled: seq 5..9 are gone. */
  body = { records: [{ seq: 10, t: 2, level: "info", source: "media", message: "b" }], maxSeq: 10, sessionId: "aa" };
  const second = await client.poll();
  assert.equal(second.missed, 5);
  /* A contiguous follow-up is not a gap. */
  body = { records: [{ seq: 11, t: 3, level: "info", source: "media", message: "c" }], maxSeq: 11, sessionId: "aa" };
  assert.equal((await client.poll()).missed, 0);
});

test("an empty response is never mistaken for a gap", async () => {
  const client = createDiagnosticsClient({
    fetch: async () => ({ ok: true, json: async () => ({ records: [], maxSeq: 40, sessionId: "aa" }) }),
  });
  await client.poll();
  assert.equal((await client.poll()).missed, 0);
});

/* ── 顶栏诊断栏 ── */

test("a healthy bar renders collapsed and reports the idle tone", () => {
  const { bar, doc } = createBar();
  assert.equal(bar.isExpanded(), false);
  assert.equal(doc.node("diagBody").hidden, true);
  assert.equal(doc.node("diagnosticsBar").dataset.tone, "idle");
  assert.equal(doc.node("diagSummary").textContent, "一切正常");
  assert.equal(doc.node("diagCount").hidden, true);
  assert.equal(doc.node("diagEmpty").hidden, false);
});

test("an error auto-expands the bar; a warning and an info line do not", () => {
  const { bar, doc } = createBar();
  bar.push("warn", "request", "bad request body");
  assert.equal(bar.isExpanded(), false, "warnings must not steal the screen");
  bar.push("info", "media", "FFmpeg started");
  assert.equal(bar.isExpanded(), false);
  bar.push("error", "ui", "播放无法恢复");
  assert.equal(bar.isExpanded(), true);
  assert.equal(doc.node("diagBody").hidden, false);
  assert.equal(doc.node("diagToggle").getAttribute("aria-expanded"), "true");
});

test("the tone and the count follow the worst record, and clearing resets them", () => {
  const { bar, log, doc } = createBar();
  bar.push("warn", "request", "w1");
  assert.equal(doc.node("diagnosticsBar").dataset.tone, "warn");
  assert.equal(doc.node("diagCount").textContent, "1");
  bar.push("error", "ui", "e1");
  assert.equal(doc.node("diagnosticsBar").dataset.tone, "error");
  assert.equal(doc.node("diagCount").textContent, "2");
  doc.node("diagClear").dispatch("click");
  assert.equal(log.size, 0);
  assert.equal(doc.node("diagnosticsBar").dataset.tone, "idle");
  assert.equal(doc.node("diagCount").hidden, true);
});

test("the summary shows the newest problem, and the hint matches its signature", () => {
  const { bar, doc } = createBar();
  bar.push("info", "media", "FFmpeg started");
  bar.push("error", "media", "FFmpeg exited with code 1: Conversion failed");
  assert.match(doc.node("diagSummary").textContent, /FFmpeg exited with code 1/);
  assert.equal(doc.node("diagHint").hidden, false);
  assert.match(doc.node("diagHint").textContent, /直播源下载中断/);
});

test("a raw media line does not displace the explainable error in the summary", () => {
  const { bar, doc } = createBar();
  bar.push("error", "media", "FFmpeg exited with code 1: Conversion failed");
  /* The backend emits FFmpeg's tail as error records too, and they arrive later. */
  bar.push("error", "media", "frame= 12 fps=0 q=0.0 size=0kB time=00:00:00.40 bitrate= 0.0kbits/s");
  assert.match(doc.node("diagSummary").textContent, /FFmpeg exited with code 1/,
    "the summary must keep the record we can explain");
  assert.equal(doc.node("diagHint").hidden, false, "and must keep its hint");
});

test("with nothing explainable the summary falls back to the newest problem", () => {
  const { bar, doc } = createBar();
  bar.push("warn", "request", "some unclassified warning");
  assert.equal(doc.node("diagSummary").textContent, "some unclassified warning");
  assert.equal(doc.node("diagHint").hidden, true);
  assert.equal(doc.node("diagHint").textContent, "");
});

test("the rendered list escapes markup instead of injecting it", () => {
  const { bar, doc } = createBar();
  bar.expand();
  bar.push("error", "ui", "<img src=x onerror=alert(1)>");
  assert.ok(doc.node("diagList").innerHTML.includes("&lt;img src=x"));
  assert.ok(!doc.node("diagList").innerHTML.includes("<img src=x"));
});

test("toggling collapses and re-expands without losing records", () => {
  const { bar, doc } = createBar();
  bar.push("error", "ui", "boom");
  assert.equal(bar.isExpanded(), true);
  doc.node("diagToggle").dispatch("click");
  assert.equal(bar.isExpanded(), false);
  assert.equal(doc.node("diagBody").hidden, true);
  doc.node("diagToggle").dispatch("click");
  assert.equal(bar.isExpanded(), true);
  assert.match(doc.node("diagList").innerHTML, /boom/);
});

test("an expanded list keeps updating once the ring is full and starts evicting", () => {
  let clock = 0;
  const log = createDiagnosticsLog({ capacity: 4, now: () => (clock += 1) });
  const doc = createDocument(BAR_IDS);
  const bar = createDiagnosticsBar({ document: doc, log, root: doc.node("diagnosticsBar") });
  bar.expand();
  for (const text of ["a", "b", "c", "d"]) bar.push("warn", "app", text);
  assert.equal(doc.node("diagList").innerHTML.match(/diag-row/g).length, 4);
  /* Adding one now evicts one: the length and the newest id both stay put. */
  bar.push("warn", "app", "e");
  const markup = doc.node("diagList").innerHTML;
  assert.equal(markup.match(/diag-row/g).length, 4);
  assert.ok(markup.includes(">e<"), "the new record must reach the DOM");
  assert.ok(!markup.includes(">a<"), "the evicted record must leave it");
});

test("the count and tone refresh even when the list body is collapsed", () => {
  const { bar, doc } = createBar();
  bar.push("warn", "app", "first");
  assert.equal(doc.node("diagCount").textContent, "1");
  bar.collapse();
  /* A collapsed bar skips the list markup, but the strip must never lag behind:
     the count and summary are the only sign that something new arrived. */
  bar.push("warn", "request", "second");
  assert.equal(bar.isExpanded(), false);
  assert.equal(doc.node("diagCount").textContent, "2");
  assert.equal(doc.node("diagSummary").textContent, "second");
  assert.equal(doc.node("diagnosticsBar").dataset.tone, "warn");
});

test("the copy button hands control to the caller", () => {
  const { copied, doc } = createBar();
  doc.node("diagCopy").dispatch("click");
  assert.equal(copied.length, 1);
});

test("a bar without its DOM, or without a log, is a null rather than a crash", () => {
  assert.equal(createDiagnosticsBar({ document: createDocument([]), log: createDiagnosticsLog() }), null);
  assert.equal(createDiagnosticsBar({ document: createDocument(BAR_IDS) }), null);
});

/* ── 更新客户端 ── */

function updateServer(routes) {
  const seen = [];
  const fetchFn = async (url, options = {}) => {
    seen.push(`${options.method || "GET"} ${url}`);
    const handler = routes[`${options.method || "GET"} ${url}`];
    if (!handler) return { ok: false, status: 404, json: async () => ({}) };
    // `http` is the transport status; the rest is the body. They are different
    // things and the client treats them differently.
    const { http = 200, ...body } = typeof handler === "function" ? handler() : handler;
    return { ok: http < 400, status: http, json: async () => body };
  };
  return { fetchFn, seen };
}

test("the update client reports the build identity alongside the update state", async () => {
  const { fetchFn } = updateServer({
    "GET /api/app-update": { version: "0.1.0", packaged: true, builtAt: "2026-09-15T04:17:38.000Z", update: { status: "idle" } },
  });
  const client = createUpdateClient({ fetch: fetchFn });
  const state = await client.status();
  assert.equal(state.version, "0.1.0");
  assert.equal(state.packaged, true);
  assert.equal(state.update.status, "idle");
});

test("a 502 from install is surfaced with its reason, not swallowed as a transport error", async () => {
  const { fetchFn } = updateServer({
    "POST /api/app-update/install": { http: 502, status: "failed", error: "checksum mismatch" },
  });
  const client = createUpdateClient({ fetch: fetchFn });
  const state = await client.install();
  assert.equal(state.update.status, "failed");
  assert.match(state.update.error, /checksum mismatch/);
});

test("a transport failure leaves the client usable and reporting failed", async () => {
  const client = createUpdateClient({ fetch: async () => { throw new Error("ECONNREFUSED"); } });
  assert.equal((await client.status()).update.status, "failed");
  assert.match((await client.check()).update.error, /ECONNREFUSED/);
  assert.equal(typeof client.get(), "object");
});

test("check and install hit the routes the main process serves", async () => {
  const { fetchFn, seen } = updateServer({
    "GET /api/app-update": { version: "0.1.0", update: { status: "idle" } },
    "POST /api/app-update/check": { status: "current" },
    "POST /api/app-update/install": { status: "ready" },
  });
  const client = createUpdateClient({ fetch: fetchFn });
  await client.status();
  await client.check();
  await client.install();
  assert.deepEqual(seen, [
    "GET /api/app-update",
    "POST /api/app-update/check",
    "POST /api/app-update/install",
  ]);
});

test("the plain-text report carries the build identity so a bug report is unambiguous", () => {
  const log = createDiagnosticsLog({ now: () => 1 });
  const text = formatDiagnostics({
    log,
    context: { "构建": "0.1.0 · 2026-09-15 12:17 · packaged", "界面语言": "zh-CN" },
    countLabel: "记录",
  });
  assert.ok(text.includes("构建: 0.1.0 · 2026-09-15 12:17 · packaged"));
});


/* ── 复制出来的诊断文本 ── */

test("the copied report carries context, counts and one line per record", () => {
  const log = createDiagnosticsLog({ now: () => 5 });
  log.merge([{ seq: 1, t: 5, level: "error", source: "media", message: "FFmpeg exited with code 1" }]);
  const text = formatDiagnostics({
    log,
    context: { "界面语言": "zh-CN", "会话状态": "error", "空值": null },
    title: "LagLingo 诊断信息",
    timeLabel: "生成时间",
    countLabel: "记录",
  });
  const lines = text.split("\n");
  assert.equal(lines[0], "LagLingo 诊断信息");
  assert.match(lines[1], /^生成时间: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
  assert.ok(lines.includes("界面语言: zh-CN"));
  assert.ok(lines.includes("会话状态: error"));
  assert.ok(!text.includes("空值"), "empty context values are omitted");
  assert.match(text, /记录: 1 \(错误 1 \/ 警告 0 \/ 信息 0\)/);
  assert.match(text, /\[错误\] media {2}FFmpeg exited with code 1/);
});

test("level labels in the report follow the caller's locale", () => {
  const log = createDiagnosticsLog({ now: () => 1 });
  log.record("warn", "request", "x");
  const text = formatDiagnostics({
    log,
    levelLabels: { info: "info", warn: "WARNED", error: "BROKEN" },
    countLabel: "Records",
  });
  assert.match(text, /\[WARNED\] request {2}x/);
  assert.match(text, /Records: 1 \(BROKEN 0 \/ WARNED 1 \/ info 0\)/);
});

/* ── 人话提示 ── */

test("known failure signatures get an actionable hint and unknown ones do not", () => {
  assert.match(hintFor("FFmpeg exited with code 1"), /停止后重新启动/);
  assert.match(hintFor("Failed to fetch"), /本地后台没有响应/);
  assert.match(hintFor("hls.js fatal: bufferAppendError"), /解码/);
  assert.match(hintFor("translations failed for 3 cues"), /翻译/);
  assert.match(hintFor("this stream requires a cookie"), /Cookie/);
  assert.equal(hintFor("something entirely new"), null);
});
