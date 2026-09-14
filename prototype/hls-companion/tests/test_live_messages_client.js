const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

class FakeElement {
  constructor() {
    this.dataset = {};
    this.children = [];
    this._innerHTML = "";
    this.innerHTMLWrites = 0;
  }
  append(node) { this.children.push(node); }
  remove() { this.removed = true; }
  querySelector() { return null; }
  set innerHTML(value) { this._innerHTML = value; this.innerHTMLWrites += 1; }
  get innerHTML() { return this._innerHTML; }
}

const {
  createLiveMessagesClient,
  createLiveMessagesTimeline,
} = require(path.resolve(__dirname, "../web-player/live-messages-client.js"));

test("LiveMessagesClient polls with afterSeq, tracks maxSeq and revision updates", async () => {
  let lastRequestedAfterSeq = null;
  const mockResponses = [
    {
      messages: [
        { id: "m1", seq: 101, revision: 1, mediaTime: 1776513600.0, author: "Alice", text: "Hello", translation: null, state: "received" },
        { id: "m2", seq: 102, revision: 1, mediaTime: 1776513601.0, author: "Bob", text: "こんにちは", translation: null, state: "received" },
      ],
      maxSeq: 102,
      stats: { state: "running", platform: "youtube", received: 2, translated: 0 },
    },
    {
      // Revision update for m2 with translation!
      messages: [
        { id: "m2", seq: 102, revision: 2, mediaTime: 1776513601.0, author: "Bob", text: "こんにちは", translation: "你好", state: "done" },
        { id: "m3", seq: 103, revision: 1, mediaTime: 1776513602.0, author: "Charlie", text: "Test", translation: null, state: "received" },
      ],
      maxSeq: 103,
      stats: { state: "running", platform: "youtube", received: 3, translated: 1 },
    },
  ];

  let pollCount = 0;
  const fetchMock = async (url, opts) => {
    const parsedUrl = new URL(url, "http://localhost:8765");
    if (parsedUrl.pathname === "/api/live-messages") {
      lastRequestedAfterSeq = parsedUrl.searchParams.get("afterSeq");
      const resp = mockResponses[pollCount++] || { messages: [], maxSeq: 103, stats: {} };
      return {
        ok: true,
        status: 200,
        json: async () => resp,
      };
    }
    if (parsedUrl.pathname === "/api/live-messages/settings" && opts?.method === "POST") {
      const body = JSON.parse(opts.body);
      return {
        ok: true,
        status: 200,
        json: async () => ({ translate: body.translate }),
      };
    }
    throw new Error("Unexpected URL: " + url);
  };

  const client = createLiveMessagesClient({ fetch: fetchMock });

  // First poll: afterSeq should be 0
  const result1 = await client.poll();
  assert.equal(lastRequestedAfterSeq, "0");
  assert.equal(result1.messages.length, 2);
  assert.equal(client.getStore().get("m1").text, "Hello");
  assert.equal(client.getStore().get("m2").text, "こんにちは");
  assert.equal(client.getStore().get("m2").translation, null);

  // Second poll: afterSeq should be 102
  const result2 = await client.poll();
  assert.equal(lastRequestedAfterSeq, "102");
  assert.equal(client.getStore().size, 3);
  // m2 in-place revision update
  assert.equal(client.getStore().get("m2").revision, 2);
  assert.equal(client.getStore().get("m2").translation, "你好");
  assert.equal(client.getStore().get("m2").state, "done");

  // Setting translation toggle
  const settingResult = await client.setTranslate(true);
  assert.equal(settingResult.translate, true);
});

test("LiveMessagesTimeline filters visibility by playbackWallTime and caps DOM lines at 500", () => {
  const timeline = createLiveMessagesTimeline({ maxDomItems: 500 });

  const messages = [
    { id: "1", seq: 1, mediaTime: 100.0, text: "msg 1" },
    { id: "2", seq: 2, mediaTime: 101.0, text: "msg 2" },
    { id: "3", seq: 3, mediaTime: 105.0, text: "msg 3 (future)" },
  ];

  // At playbackWallTime = 100.5s:
  // 1 (100.0 <= 100.75) -> visible
  // 2 (101.0 > 100.75) -> invisible
  // 3 (105.0 > 100.75) -> invisible
  const visibleAt100_5 = timeline.getVisibleMessages(messages, 100.5);
  assert.equal(visibleAt100_5.length, 1);
  assert.equal(visibleAt100_5[0].id, "1");
  const firstRender = timeline.render(100.5, messages);
  const unchangedRender = timeline.render(100.5, messages);
  assert.equal(firstRender.changed, true);
  assert.equal(unchangedRender.changed, false);

  // At playbackWallTime = 101.0s:
  // 2 (101.0 <= 101.25) -> visible
  const visibleAt101 = timeline.getVisibleMessages(messages, 101.0);
  assert.equal(visibleAt101.length, 2);
  assert.equal(visibleAt101[1].id, "2");

  // DOM cap at 500: create 600 past messages
  const many = [];
  for (let i = 0; i < 600; i++) {
    many.push({ id: `m_${i}`, seq: i, mediaTime: 50 + i * 0.01, text: `txt ${i}` });
  }
  const capped = timeline.getVisibleMessages(many, 200.0);
  assert.equal(capped.length, 500);
  assert.equal(capped[capped.length - 1].id, "m_599");
});

test("LiveMessagesTimeline renders canonical object authors without throwing", () => {
  const originalDocument = global.document;
  global.document = { createElement: () => new FakeElement() };
  try {
    const container = new FakeElement();
    const timeline = createLiveMessagesTimeline({ container });
    const result = timeline.render(101, [{
      id: "youtube:1",
      seq: 1,
      revision: 1,
      mediaTime: 100,
      text: "hello",
      author: { name: "Alice", badges: ["Member"] },
    }]);
    assert.equal(result.rows.length, 1);
    assert.equal(container.children.length, 1);
    assert.match(container.children[0].innerHTML, /Alice/);
    assert.match(container.children[0].innerHTML, /Member/);
  } finally {
    global.document = originalDocument;
  }
});

test("LiveMessagesClient prunes expired and excess browser messages", async () => {
  const response = {
    messages: Array.from({ length: 8 }, (_, index) => ({
      id: `m${index}`,
      seq: index + 1,
      revision: 1,
      mediaTime: 100 + index,
      text: `message ${index}`,
    })),
    maxSeq: 8,
    stats: {},
  };
  const client = createLiveMessagesClient({
    request: async () => response,
    retentionSeconds: 3,
    maxMessages: 4,
  });
  await client.poll();
  assert.deepEqual([...client.getStore().keys()], ["m4", "m5", "m6", "m7"]);

  const revisionClient = createLiveMessagesClient({
    request: async () => ({
      messages: [{ id: "old", seq: 9, revision: 2, mediaTime: 100, text: "old", translation: "translated" }],
      maxSeq: 9,
      stats: {},
    }),
    retentionSeconds: 3,
    maxMessages: 4,
  });
  await revisionClient.poll();
  assert.equal(revisionClient.getStore().get("old")?.translation, "translated");
});

test("LiveMessagesTimeline leaves unchanged DOM rows untouched and patches only revisions", async () => {
  const originalDocument = global.document;
  global.document = { createElement: () => new FakeElement() };
  try {
    const container = new FakeElement();
    const timeline = createLiveMessagesTimeline({ container });
    const message = { id: "m1", seq: 1, revision: 1, mediaTime: 100, text: "hello", author: "Alice" };
    timeline.render(101, [message]);
    const row = container.children[0];
    const initialHtml = row.innerHTML;
    timeline.render(101, [message]);
    assert.equal(row.innerHTML, initialHtml);
    assert.equal(row.innerHTMLWrites, 1);

    timeline.render(101, [{ ...message, revision: 2, translationState: "done", translation: "你好" }]);
    assert.equal(row.innerHTMLWrites, 2);
    assert.match(row.innerHTML, /你好/);
  } finally {
    global.document = originalDocument;
  }
});

test("LiveMessagesTimeline requests follow only when visible content changes", () => {
  const timeline = createLiveMessagesTimeline();
  const rows = [{ id: "m1", seq: 1, revision: 1, mediaTime: 100, text: "hello" }];
  let followRequests = 0;
  const renderAndFollow = () => {
    const result = timeline.render(101, rows);
    if (result.changed) followRequests += 1;
  };
  renderAndFollow();
  renderAndFollow();
  assert.equal(followRequests, 1);
  rows[0] = { ...rows[0], seq: 2, revision: 2, translationState: "done", translation: "你好" };
  renderAndFollow();
  assert.equal(followRequests, 2);
});

test("repeated starts do not duplicate an in-flight polling loop", async () => {
  let requests = 0;
  let resolve;
  const client = createLiveMessagesClient({ request: () => { requests++; return new Promise(r => { resolve = r; }); } });
  client.startPolling();
  client.startPolling();
  assert.equal(requests, 1);
  client.stopPolling();
  resolve({ messages: [], maxSeq: 0 });
  await new Promise(r => setImmediate(r));
  assert.equal(requests, 1);
});
