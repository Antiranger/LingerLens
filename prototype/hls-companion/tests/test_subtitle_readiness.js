const test = require("node:test");
const assert = require("node:assert/strict");
const { playerContext, spy, stubElement, deferred } = require("./player-harness.js");

function budget() {
  let now = 100000;
  const elements = new Map();
  const el = id => { if (!elements.has(id)) elements.set(id, stubElement()); return elements.get(id); };
  el('targetDelay').value = '15';
  const samples = new Map();
  const video = { paused: false, seeking: false, playbackRate: 1, readyState: 4 };
  const h = playerContext({
    state: { subtitleBudget: { lowSince: null, suggested: null, primed: true },
      uiGeneration: 1, subtitleAfterSeq: 0, subtitleMaxKnownEnd: 0, observedMediaSessionId: 'A' },
    globals: { el, subtitleScheduler: null, subtitleReadiness: samples, video,
      document: { hidden: false }, subtitlePrefs: { enabled: true, offset: 0 },
      lastSessionState: 'running', performance: { now: () => now },
      request: spy(async () => ({})), showError: spy(), localStorage: { setItem: spy() },
      window: {}, subtitleCues: new Map(), playingWallClock: () => 100, console,
      cueDurationPercentiles: () => ({ p50: 3, p95: 40 }),
      seconds: String, updateLabel: (_key, fallback) => fallback },
    functions: ['updateSubtitleBudget', 'subtitleLeadStats', 'resetSubtitleBudget', 'subtitleBudgetContext',
      'applySuggestedDelay', 'recordSubtitleReadiness', 'refreshSubtitles'],
  });
  return { ...h, el, video, samples, setNow: value => { now = value; },
    sample: (id, at, lead) => samples.set(id, { translation: { monotonic: at, leadSeconds: lead,
      eligible: true, paused: false, seeking: false, hidden: false, playbackRate: 1, offset: 0, target: 15 } }) };
}

test('unrelated historical percentiles cannot recommend extra delay', () => {
  const h = budget();
  const stats = { translationSuccessReadyLagP50: 20, translationSuccessReadyLagP95: 40 };
  h.call('updateSubtitleBudget', stats, 15, 15);
  h.setNow(140000);
  h.call('updateSubtitleBudget', stats, 15, 15);
  assert.equal(h.el('budgetMargin').textContent, '—');
  assert.equal(h.el('applyDelayButton').hidden, true);
});

test('margin is the measured per-cue lead, independent of sentence length percentiles', () => {
  const h = budget();
  for (let id = 0; id < 10; id++) h.sample(id, 99000 - id * 1000, 2);
  h.call('updateSubtitleBudget', {}, 15, 15);
  assert.equal(h.el('budgetMargin').textContent, '2');
  assert.equal(h.el('applyDelayButton').hidden, true);
});

test('sustained fresh lateness recommends an absolute target, then expires without new cues', () => {
  const h = budget();
  for (let id = 0; id < 5; id++) h.sample(id, 96000 + id * 1000, -2);
  h.call('updateSubtitleBudget', {}, 15, 15);
  for (let i = 1; i <= 7; i++) {
    h.setNow(100000 + i * 5000); h.sample(4+i, 100000 + i * 5000, -2);
    h.call('updateSubtitleBudget', {}, 15, 15);
  }
  assert.equal(h.el('applyDelayButton').hidden, false);
  assert.equal(h.read().subtitleBudget.suggested, 19);
  assert.match(h.el('applyDelayButton').textContent, /调到 19/);
  h.setNow(151000);
  h.call('updateSubtitleBudget', {}, 15, 15);
  assert.equal(h.el('applyDelayButton').hidden, true);
  assert.equal(h.el('budgetMargin').textContent, '—');
});

test('pause, seek, background and catch-up never advise increasing delay', () => {
  for (const overrides of [{ paused: true }, { seeking: true }, { playbackRate: 1.08 }, { readyState: 2 }, { hidden: true }]) {
    const h = budget(); Object.assign(h.video, overrides);
    h.context.document.hidden = !!overrides.hidden;
    for (let id=0; id<10; id++) h.sample(id, 99000, -8);
    h.call('updateSubtitleBudget', {}, 15, 15);
    assert.equal(h.el('applyDelayButton').hidden, true);
    assert.equal(h.el('budgetMargin').textContent, '—');
  }
});

test('a polling gap or changed provider cannot turn old deficits into a recommendation', () => {
  for (const changeProvider of [false, true]) {
    const h = budget();
    for (let id = 0; id < 5; id++) h.sample(id, 100000, -2);
    h.call('updateSubtitleBudget', { translationProviderId: 'first' }, 15, 15);
    h.setNow(135000); h.sample(5, 135000, -2);
    h.call('updateSubtitleBudget', { translationProviderId: changeProvider ? 'second' : 'first' }, 15, 15);
    assert.equal(h.el('applyDelayButton').hidden, true);
  }
});

test('offset and target changes discard observations made against the previous settings', () => {
  for (const changeOffset of [false, true]) {
    const h = budget();
    for (let id = 0; id < 5; id++) h.sample(id, 100000, -2);
    h.call('updateSubtitleBudget', {}, 15, 15);
    if (changeOffset) h.context.subtitlePrefs.offset = 1;
    else h.el('targetDelay').value = '20';
    h.call('updateSubtitleBudget', {}, 15, 20);
    assert.equal(h.el('budgetMargin').textContent, '—');
    assert.equal(h.samples.size, 0);
  }
});

test('initial snapshot is ineligible; subsequent arrivals include the subtitle offset', async () => {
  const h = budget();
  h.call('resetSubtitleBudget');
  h.context.subtitlePrefs.offset = 2;
  let seq = 0;
  h.context.request = async () => ({ mediaSessionId: 'A', maxSeq: ++seq, cues: [
    { id: seq, seq, tStart: 105, tEnd: 107, hold: 1, src: 'hello', zh: '你好', state: 'done' },
  ] });
  await h.call('refreshSubtitles');
  await h.call('refreshSubtitles');
  assert.equal(h.samples.get(1).translation.eligible, false);
  assert.equal(h.samples.get(2).translation.eligible, true);
  assert.equal(h.samples.get(2).translation.leadSeconds, 3);
});

test('applying a recommendation sets its absolute value and ignores a late reply from the previous session', async () => {
  for (const switchSession of [false, true]) {
    const h = budget();
    for (let id = 0; id < 5; id++) h.sample(id, 100000, -2);
    for (let i = 0; i <= 7; i++) {
      h.setNow(100000 + i * 5000); h.sample(i + 5, 100000 + i * 5000, -2);
      h.call('updateSubtitleBudget', {}, 15, 15);
    }
    const pending = deferred();
    h.context.request = spy(() => pending.promise);
    const apply = h.call('applySuggestedDelay');
    assert.equal(h.context.request.calls[0][0], '/api/target-delay');
    assert.equal(h.context.request.calls[0][1].seconds, 19);
    if (switchSession) h.write({ uiGeneration: 2 });
    pending.resolve({ ok: true });
    await apply;
    assert.equal(h.el('targetDelay').value, switchSession ? '15' : '19');
    if (!switchSession) {
      assert.match(h.el('message').textContent, /继续观察/);
      assert.equal(h.el('applyDelayButton').hidden, true);
    }
  }
});

test('a recommendation never lowers a target already at or above its supported ceiling', () => {
  const h = budget();
  h.el('targetDelay').value = '65';
  for (let i = 0; i < 12; i++) {
    const at = 100000 + i * 5000;
    h.setNow(at); h.sample(i, at, -2);
    h.samples.get(i).translation.target = 65;
    h.call('updateSubtitleBudget', {}, 65, 65);
  }
  assert.equal(h.el('budgetMargin').textContent, '-2');
  assert.equal(h.el('applyDelayButton').hidden, true);
});

test("a subtitle response from before Stop cannot repopulate the cleared session", async () => {
  const pending = deferred();
  const cues = new Map();
  const h = playerContext({
    state: { uiGeneration: 1, subtitleAfterSeq: 0, subtitleMaxKnownEnd: 0, observedMediaSessionId: "old" },
    globals: { subtitlePrefs: { enabled: true }, window: {}, subtitleCues: cues, request: () => pending.promise,
      recordSubtitleReadiness: spy(), playingWallClock: () => null, console },
    functions: ["refreshSubtitles"],
  });
  const poll = h.call("refreshSubtitles");
  h.write({ uiGeneration: 2, observedMediaSessionId: null });
  pending.resolve({ cues: [{ id: 1, seq: 99, tStart: 1, tEnd: 3, hold: 1, src: "old" }], maxSeq: 99 });
  await poll;
  assert.equal(cues.size, 0);
  assert.equal(h.read().subtitleAfterSeq, 0);
});

test("source and translation arrivals keep their own measured playheads", () => {
  const samples = new Map();
  let wall = 100;
  const video = { paused: false, seeking: false, playbackRate: 1 };
  const h = playerContext({ globals: { subtitleReadiness: samples, video, document: { hidden: false }, playingWallClock: () => wall,
    performance: { now: () => 1000 }, subtitleBudgetContext: () => true,
    subtitleBudget: { primed: true }, subtitlePrefs: { offset: 0 }, el: () => ({ value: "15" }) },
    functions: ["recordSubtitleReadiness"] });
  h.call("recordSubtitleReadiness", { id: 1, generation: 2, tStart: 110, src: "hello" });
  wall = 112;
  h.call("recordSubtitleReadiness", { id: 1, generation: 2, tStart: 110, src: "hello", zh: "你好", state: "done" });
  assert.equal(samples.get(1).source.leadSeconds, 10);
  assert.equal(samples.get(1).translation.leadSeconds, -2);
  video.paused = true;
  for (let id = 2; id < 1000; id++) h.call("recordSubtitleReadiness", { id, tStart: 113, src: "hello" });
  assert.equal(samples.size, 512);
  assert.equal(samples.get(999).source.paused, true);
});

test("a different server session cannot advance the old subtitle cursor", async () => {
  const cues = new Map();
  const h = playerContext({
    state: { uiGeneration: 1, observedMediaSessionId: "A", subtitleAfterSeq: 7, subtitleMaxKnownEnd: 0 },
    globals: { subtitlePrefs: { enabled: true }, window: {}, subtitleCues: cues, playingWallClock: () => null, console,
      request: async () => ({ mediaSessionId: "B", maxSeq: 50, cues: [{ id: 1, seq: 50 }] }), recordSubtitleReadiness: spy() },
    functions: ["refreshSubtitles"],
  });
  await h.call("refreshSubtitles");
  assert.equal(h.read().subtitleAfterSeq, 7);
  assert.equal(cues.size, 0);
});
