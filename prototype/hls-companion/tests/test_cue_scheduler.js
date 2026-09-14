const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { createSubtitleScheduler, mergeCueBySeq } = require(path.resolve(__dirname, "../web-player/subtitle-scheduler.js"));

function cue(id, tStart, tEnd, state = "done", hold = 2, zh = `译${id}`) {
  return { id, tStart, tEnd, hold, state, src: `原${id}`, zh };
}

test("a missing earlier translation never shifts later sentences or rewinds playback when it arrives", () => {
  const scheduler = createSubtitleScheduler();
  const slow = { ...cue(1, 0, 2, "translating"), speaker: "1" };
  const second = { ...cue(2, 2, 4), speaker: "1" };
  const third = { ...cue(3, 4, 6), speaker: "1" };
  assert.deepEqual(scheduler.active([slow, second, third], 2).map(x => x.id), [2]);
  slow.state = "done";
  assert.deepEqual(scheduler.active([slow, second, third], 4).map(x => x.id), [3]);
  assert.deepEqual([second.tStart, third.tStart], [2, 4]);
  assert.deepEqual(scheduler.active([slow, second, third], 6.5).map(x => x.id), [3]);
});

test("an expired catch-up caption yields to current speech even without speaker labels", () => {
  const scheduler = createSubtitleScheduler();
  const late = cue(1, 0, 1, "done", 7);
  const current = cue(2, 3, 5);
  assert.deepEqual(scheduler.active([late], 3).map(x => x.id), [1]);
  assert.deepEqual(scheduler.active([late, current], 3.1).map(x => x.id), [2]);
  assert.deepEqual(scheduler.active([late, current], 4).map(x => x.id), [2]);
  assert.deepEqual(scheduler.active([late, current], 9).map(x => x.id), []);
});

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

test("never displays pending, failed or empty translations", () => {
  const scheduler = createSubtitleScheduler();
  assert.equal(scheduler.pick([cue(1, 8, 10, "src")], 9), null);
  assert.equal(scheduler.pick([cue(1, 8, 10, "translating")], 9), null);
  assert.equal(scheduler.pick([cue(1, 8, 10, "failed", 2, null)], 9), null);
  assert.equal(scheduler.pick([cue(2, 8, 10, "done", 2, "   ")], 9), null);
});

// The video overlay renders through active(), not pick(). media-clock.js used to
// carry a second cue-visibility rule that accepted state "failed"; it was dead
// code in the browser (workbench-controller.js is loaded later) but it disagreed
// with this one, so pin the live rule on the path the overlay actually uses.
test("the overlay path rejects the same states as the timeline path", () => {
  const scheduler = createSubtitleScheduler();
  assert.deepEqual(scheduler.active([cue(1, 8, 10, "src")], 9), []);
  assert.deepEqual(scheduler.active([cue(1, 8, 10, "translating")], 9), []);
  assert.deepEqual(scheduler.active([cue(1, 8, 10, "failed", 2, null)], 9), []);
  assert.deepEqual(scheduler.active([cue(2, 8, 10, "done", 2, "   ")], 9), []);
  assert.deepEqual(scheduler.active([cue(3, 8, 10)], 9).map((c) => c.id), [3]);
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

test("retime re-evaluates cached admission after the effective playhead moves", () => {
  const scheduler = createSubtitleScheduler({ maxLateSeconds: 2, maxTail: 1.5 });
  const item = cue(1, 100, 101, "done", 1);
  assert.equal(scheduler.pick([item], 104.6), null, "dropped at the old offset");
  assert.equal(scheduler.stats.droppedLateCues, 1);

  scheduler.retime();
  assert.equal(scheduler.pick([item], 101.6).id, 1, "visible immediately at the new offset");
  assert.equal(scheduler.stats.droppedLateCues, 1, "retiming preserves session telemetry");
});

test("overlapping newer cue waits for minDwell in the single-line compatibility picker", () => {
  const scheduler = createSubtitleScheduler({ minDwell: 1.2 });
  const first = cue(1, 6, 10, "done", 4);
  const second = cue(2, 6.5, 10.5, "done", 4);
  assert.equal(scheduler.pick([first], 10.0).id, 1);
  assert.equal(scheduler.pick([first, second], 10.5).id, 1);
  assert.equal(scheduler.pick([first, second], 11.21).id, 2);
});

test("active returns every overlapping cue in deterministic start order", () => {
  const scheduler = createSubtitleScheduler();
  const first = { ...cue(1, 6, 10, "done", 4), speaker: "1" };
  const second = { ...cue(2, 7, 11, "done", 4), speaker: "2" };
  const sameStart = { ...cue(3, 7, 10.5, "done", 4), speaker: "3" };
  assert.deepEqual(scheduler.active([second, sameStart, first], 9).map((item) => item.id), [1, 3, 2]);
  assert.deepEqual(scheduler.active([first, second], 11.4).map((item) => item.id), [2]);
  assert.deepEqual(scheduler.active([first, second], 12.6).map((item) => item.id), []);
});

test("a non-overlapping successor owns the display after its start instead of stacking on the previous tail", () => {
  const scheduler = createSubtitleScheduler({ maxTail: 1.5 });
  const previous = cue(1, 5, 8, "done", 1.5);
  const next = cue(2, 8.4, 10, "done", 1.5);
  assert.deepEqual(scheduler.active([previous, next], 8.39).map((item) => item.id), [1]);
  assert.deepEqual(scheduler.active([previous, next], 8.4).map((item) => item.id), [2]);
  assert.deepEqual(scheduler.stats, { lateCues: 0, droppedLateCues: 0, sourceOnlyCues: 0 });
});

test("a contiguous successor owns the exact shared boundary", () => {
  const scheduler = createSubtitleScheduler({ maxTail: 1.5 });
  const previous = cue(1, 5, 8, "done", 1.5);
  const next = cue(2, 8, 10, "done", 1.5);
  assert.deepEqual(scheduler.active([previous, next], 8).map((item) => item.id), [2]);
});

test("a successor that starts before the previous audio ends remains a genuine overlap", () => {
  const scheduler = createSubtitleScheduler({ maxTail: 1.5 });
  const previous = { ...cue(1, 5, 8, "done", 1.5), speaker: "1" };
  const overlapping = { ...cue(2, 7.5, 10, "done", 1.5), speaker: "2" };
  assert.deepEqual(scheduler.active([previous, overlapping], 7.75).map((item) => item.id), [1, 2]);
  assert.deepEqual(scheduler.active([previous, overlapping], 8).map((item) => item.id), [2]);
});

test("a newer chunk from the same speaker replaces an impossible timestamp overlap", () => {
  const scheduler = createSubtitleScheduler({ maxTail: 1.5 });
  const previous = { ...cue(1, 5, 8, "done", 1.5), speaker: "1", chunkOrder: 1 };
  const continuation = { ...cue(2, 7.5, 10, "done", 1.5), speaker: "1", chunkOrder: 2 };
  assert.deepEqual(scheduler.active([previous, continuation], 7.49).map((item) => item.id), [1]);
  assert.deepEqual(scheduler.active([previous, continuation], 7.5).map((item) => item.id), [2]);
});

test("distinct same-speaker cues with the same start are both shown", () => {
  const scheduler = createSubtitleScheduler({ maxTail: 1.5 });
  const fragment = {
    ...cue(20, 52.199, 53.579, "done", 1.2, "也搜不出来，所以"),
    src: "も出てこないから、", speaker: "1", chunkOrder: 20,
  };
  const continuation = {
    ...cue(21, 52.199, 58.559, "done", 2.16, "等、Kiriro……"),
    src: "ちょ、きりろ...", speaker: "1", chunkOrder: 21,
  };

  assert.deepEqual(scheduler.active([fragment, continuation], 52.5).map((item) => item.id), [20, 21]);
  assert.deepEqual(scheduler.active([fragment, continuation], 53.6).map((item) => item.id), [21]);
});

test("a dropped stale continuation does not hide a still-readable same-speaker cue", () => {
  const scheduler = createSubtitleScheduler({ maxTail: 7, maxLateSeconds: 2 });
  const readable = { ...cue(1, 0, 1, "done", 7), speaker: "1", chunkOrder: 1 };
  const stale = { ...cue(2, 2, 3, "done", 1), speaker: "1", chunkOrder: 2 };
  assert.deepEqual(scheduler.active([readable, stale], 7).map((item) => item.id), [1]);
});

test("an earlier overlapping cue leaves when its audio ends while the later cue keeps its own tail", () => {
  const scheduler = createSubtitleScheduler({ maxTail: 1.5 });
  const early = { ...cue(1, 5, 8, "done", 1), speaker: "1" };
  const later = { ...cue(2, 7, 10, "done", 2), speaker: "2" };
  assert.deepEqual(scheduler.active([early, later], 7.5).map((item) => item.id), [1, 2]);
  assert.deepEqual(scheduler.active([early, later], 8).map((item) => item.id), [2]);
  assert.deepEqual(scheduler.active([early, later], 9.1).map((item) => item.id), [2]);
  assert.deepEqual(scheduler.active([early, later], 11.5).map((item) => item.id), [2]);
});

test("scheduler forgets admissions for cues no longer retained by the caller", () => {
  const scheduler = createSubtitleScheduler();
  for (let batch = 0; batch < 20; batch += 1) {
    const cues = Array.from({ length: 100 }, (_, index) => {
      const id = batch * 100 + index;
      return cue(id, id, id + 0.5);
    });
    scheduler.pick(cues, batch * 100 + 99);
  }
  assert.ok(scheduler.debugState().admissionCount <= 100);
});

test("bridges gaps shorter than 300ms without clearing", () => {
  const scheduler = createSubtitleScheduler({ bridgeGap: 0.3, maxTail: 1 });
  const first = cue(1, 9, 10, "done", 1);
  const second = cue(2, 11.2, 11.2, "done", 1);
  assert.equal(scheduler.pick([first, second], 10.0).id, 1);
  assert.equal(scheduler.pick([first, second], 11.05).id, 1);
  assert.equal(scheduler.pick([first, second], 11.2).id, 2);
});
