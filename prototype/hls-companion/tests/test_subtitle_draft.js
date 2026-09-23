/*
 * The draft line: what the backend has recognized but has not released as a cue.
 *
 * These run the SHIPPED player.js functions, not a model of them. The two things
 * that matter are that the line never gets ahead of the viewer, and that it never
 * appears next to the cue that replaced it.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const { playerContext } = require("./player-harness.js");

function draftMode(prefs = { enabled: true, mode: "bilingual", offset: 0 }) {
  return playerContext({
    state: {},
    globals: { subtitlePrefs: prefs },
    functions: ["usableDraft", "draftLine", "foldForDraft"],
    constants: ["SUBTITLE_DRAFT_STALE_SECONDS"],
  });
}

const held = { text: "こんにちは世界", tStart: 100, tEnd: 104, itemId: "7", lang: "ja", speaker: null };

test("a draft the backend cannot place on the timeline is dropped", () => {
  const h = draftMode();
  assert.equal(h.call("usableDraft", null), null);
  assert.equal(h.call("usableDraft", { ...held, text: "   " }), null);
  assert.equal(h.call("usableDraft", { ...held, tStart: null }), null);
  assert.equal(h.call("usableDraft", { ...held, tEnd: "soon" }), null);
  assert.equal(h.call("usableDraft", held), held);
});

test("a draft is shown from the moment the playhead reaches its sentence", () => {
  const h = draftMode();
  const cue = { src: "さようなら" };
  assert.equal(h.call("draftLine", held, 99.5, [cue]), "");
  assert.equal(h.call("draftLine", held, 100.0, [cue]), "こんにちは世界");
  assert.equal(h.call("draftLine", held, 103.0, [cue]), "こんにちは世界");
});

test("a draft is dropped once the playhead has run past what the backend proved", () => {
  const h = draftMode();
  const stale = h.call("usableDraft", { ...held, tEnd: 100.0 });
  assert.equal(
    h.call("draftLine", stale, 101.0, []),
    "こんにちは世界",
    "the backend's own playhead reference is a poll old, so a fresh line must survive that gap",
  );
  assert.equal(
    h.call("draftLine", stale, 130.0, []),
    "",
    "a seek, or a playhead the backend has not been told about, ends the line",
  );
});

test("a replaced projection never doubles the line", () => {
  const h = draftMode();
  // The cue carries exactly the held words, and one carrying more of the same
  // sentence: both mean the backend has released a cue for this text.
  assert.equal(h.call("draftLine", held, 102, [{ src: "こんにちは世界" }]), "");
  assert.equal(h.call("draftLine", held, 102, [{ src: "こんにちは世界、さようなら" }]), "");
  assert.equal(h.call("draftLine", held, 102, [{ src: "こんにちは 世界" }]), "",
    "spacing the Provider inserted is not a different sentence");
  // A cue for the first half of a long turn is a different caption: the tail is
  // still held, and the viewer is reading it now.
  assert.equal(h.call("draftLine", held, 102, [{ src: "さようなら" }]), "こんにちは世界");
});

test("translation-only mode has no draft to show", () => {
  const h = draftMode({ enabled: true, mode: "zh", offset: 0 });
  assert.equal(h.call("draftLine", held, 102, []), "", "the held text is untranslated source");
});

test("a missing or blank draft leaves no line behind", () => {
  const h = draftMode();
  assert.equal(h.call("draftLine", null, 102, []), "");
  assert.equal(h.call("foldForDraft", "　Ａ　ｂ　"), "Ab", "full width and spacing both go, case does not");
});
