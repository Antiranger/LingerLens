const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { createSubtitleScheduler, mergeCueBySeq } = require(path.resolve(__dirname, "../web-player/subtitle-scheduler.js"));

function cue(id, tStart, tEnd, state = "done", hold = 2, zh = `译${id}`) {
  return { id, tStart, tEnd, hold, state, src: `原${id}`, zh };
}

test("higher-seq same-cue revision preserves text and schedule fields", () => {
  const cues = new Map();
  const original = { ...cue(7, 3, 10), seq: 4, state: "src" };
  const revised = { ...original, seq: 5, state: "done" };
  assert.equal(mergeCueBySeq(cues, original), true);
  assert.equal(mergeCueBySeq(cues, revised), true);
  assert.equal(cues.size, 1);
  assert.deepEqual(cues.get(7), revised);
  assert.equal(mergeCueBySeq(cues, { ...original, state: "failed" }), false);
  const scheduler = createSubtitleScheduler();
  assert.equal(scheduler.pick([...cues.values()], 5).id, 7);
  assert.equal(cues.get(7).src, original.src);
  assert.equal(cues.get(7).tStart, original.tStart);
});

test("never displays src/translating cues", () => {
  const scheduler = createSubtitleScheduler();
  assert.equal(scheduler.pick([cue(1, 8, 10, "src")], 9), null);
  assert.equal(scheduler.pick([cue(1, 8, 10, "translating")], 9), null);
  assert.equal(scheduler.pick([cue(1, 8, 10, "failed", 2, null)], 9).id, 1);
});

test("shows the whole sentence from tStart and holds it through tEnd", () => {
  const scheduler = createSubtitleScheduler();
  const item = cue(1, 3, 10);
  assert.equal(scheduler.pick([item], 2.9), null, "not yet spoken");
  assert.equal(scheduler.pick([item], 3.0).id, 1, "appears as the speaker starts");
  assert.equal(scheduler.pick([item], 6.5).id, 1, "still up mid-sentence");
  assert.equal(scheduler.pick([item], 10.0).id, 1, "still up at the sentence end");
});

test("a translation landing mid-sentence shows immediately, not at the next cue", () => {
  const scheduler = createSubtitleScheduler();
  const item = cue(1, 3, 10);
  assert.equal(scheduler.pick([item], 7.0).id, 1);
});

test("falls back to tEnd when a cue has no start boundary", () => {
  const scheduler = createSubtitleScheduler();
  const item = { ...cue(1, null, 10), tStart: null };
  assert.equal(scheduler.pick([item], 9.9), null);
  assert.equal(scheduler.pick([item], 10.0).id, 1);
});

test("the tail is capped so a long hold cannot outlive the next line", () => {
  const scheduler = createSubtitleScheduler({ maxTail: 1.5 });
  const item = cue(1, 3, 10, "done", 7);
  assert.equal(scheduler.pick([item], 11.5).id, 1);
  assert.equal(scheduler.pick([item], 11.6), null);
});

test("a following contiguous cue takes over immediately at its tStart", () => {
  const scheduler = createSubtitleScheduler({ minDwell: 0 });
  const first = cue(1, 3, 10);
  const second = cue(2, 10, 14);
  assert.equal(scheduler.pick([first, second], 9.9).id, 1);
  assert.equal(scheduler.pick([first, second], 10.0).id, 2);
});

test("shows mildly late cues from now and drops overly late cues", () => {
  const show = createSubtitleScheduler({ maxLateSeconds: 2 });
  assert.equal(show.pick([cue(1, 9, 10, "done", 1)], 12.5).id, 1);
  assert.equal(show.stats.lateCues, 1);

  const drop = createSubtitleScheduler({ maxLateSeconds: 2 });
  assert.equal(drop.pick([cue(1, 9, 10, "done", 1)], 13.1), null);
  assert.equal(drop.stats.droppedLateCues, 1);
});

test("overlapping newer cue waits for minDwell", () => {
  const scheduler = createSubtitleScheduler({ minDwell: 1.2 });
  const first = cue(1, 6, 10, "done", 4);
  const second = cue(2, 6.5, 10.5, "done", 4);
  assert.equal(scheduler.pick([first], 10.0).id, 1);
  assert.equal(scheduler.pick([first, second], 10.5).id, 1);
  assert.equal(scheduler.pick([first, second], 11.21).id, 2);
});

test("bridges gaps shorter than 300ms without clearing", () => {
  const scheduler = createSubtitleScheduler({ bridgeGap: 0.3, maxTail: 1 });
  const first = cue(1, 9, 10, "done", 1);
  const second = cue(2, 11.2, 11.2, "done", 1);
  assert.equal(scheduler.pick([first, second], 10.0).id, 1);
  assert.equal(scheduler.pick([first, second], 11.05).id, 1);
  assert.equal(scheduler.pick([first, second], 11.2).id, 2);
});
