/*
 * One Profile is recommended, it leads the list, and a star says why.
 *
 * Soniox (stt-rt-v5 with translation) and Qwen LiveTranslate both produce
 * bilingual subtitles on the ASR session itself, so no translation model is
 * configured -- but two stars side by side read as "these are the same", which
 * is not a ranking at all, so the mark belongs to Soniox alone. "Recommended"
 * still has to survive two things a persisted flag alone would not:
 *
 *  - a Profile the user created themselves, which carries no `recommended`
 *    field at all, and
 *  - a catalog whose order is whatever the user's file happens to hold.
 *
 * So the judgement is made from the PROTOCOL, and the sort is applied at render
 * time. These tests drive the shipped functions rather than a model of them.
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const { playerContext, spy } = require("./player-harness.js");

const TEST_TIMEOUT_MS = 5000;

function providers() {
  const globals = {
    updateLabel: (key, fallback) => fallback || key,
    escapeHtml: (value) => String(value),
    checked: (value) => (value ? " checked" : ""),
    profileText: (id, fallback) => fallback || id,
  };
  const context = playerContext({
    globals,
    constants: ["RECOMMENDED_KINDS", "RECOMMENDATION_DECIDED_KINDS"],
    functions: [
      "isRecommended",
      "recommendedFirst",
      "recommendedBadge",
      "providerHasCredential",
      "providerOption",
      "roleProviders",
    ],
  });
  return { ...context, globals };
}

test("a profile the user made is still recognised by its protocol", { timeout: TEST_TIMEOUT_MS }, () => {
  const h = providers();
  // No `recommended` flag: this is a hand-made profile.
  assert.equal(
    h.call("isRecommended", { kind: "soniox-realtime", options: {} }),
    true,
  );
  assert.equal(
    h.call("isRecommended", { kind: "dashscope-livetranslate-realtime" }),
    false,
    "the star is Soniox's alone; a second one would not rank anything",
  );
  assert.equal(h.call("isRecommended", { kind: "deepgram-streaming" }), false);
  // The flag still counts for a preset that carries it.
  assert.equal(h.call("isRecommended", { kind: "openai-completions", recommended: true }), true);
  // A record added while the flag said otherwise keeps that stale flag in the
  // user's file: for a protocol the app has decided about, it must not win.
  assert.equal(
    h.call("isRecommended", { kind: "dashscope-livetranslate-realtime", recommended: true }),
    false,
    "a persisted recommended: true cannot bring back a second star",
  );
  assert.equal(
    h.call("isRecommended", { kind: "soniox-realtime", recommended: false }),
    true,
    "nor can a persisted recommended: false hide the one the app stars",
  );
  assert.equal(h.call("isRecommended", undefined), false);
});

test("the recommended profiles are ranked first, whatever order the file holds", { timeout: TEST_TIMEOUT_MS }, () => {
  const h = providers();
  const catalog = [
    { id: "deepgram", kind: "deepgram-streaming" },
    { id: "fun-asr", kind: "dashscope-task-asr" },
    { id: "my-soniox", kind: "soniox-realtime" },
    { id: "assemblyai", kind: "assemblyai-streaming" },
    { id: "qwen-live", kind: "dashscope-livetranslate-realtime" },
  ];
  const ordered = h.call("recommendedFirst", catalog).map((provider) => provider.id);
  assert.deepEqual(ordered, ["my-soniox", "deepgram", "fun-asr", "assemblyai", "qwen-live"]);
  assert.deepEqual(
    catalog.map((provider) => provider.id),
    ["deepgram", "fun-asr", "my-soniox", "assemblyai", "qwen-live"],
    "the caller's array must not be reordered in place",
  );
});

test("the role selector keeps every credentialed profile and still ranks first", { timeout: TEST_TIMEOUT_MS }, () => {
  const h = providers();
  const group = {
    providers: [
      { id: "a", kind: "deepgram-streaming", apiKeyConfigured: true },
      { id: "b", kind: "soniox-realtime", apiKeyConfigured: true },
      { id: "c", kind: "dashscope-livetranslate-realtime" },
    ],
  };
  const listed = h.call("roleProviders", group, ["c"]).map((provider) => provider.id);
  assert.deepEqual(listed, ["b", "a", "c"], "credential filter first, recommended order second");
  assert.deepEqual(
    h.call("roleProviders", group, []).map((provider) => provider.id),
    ["b", "a"],
    "an unreferenced profile without a key stays hidden",
  );
});

test("the star is rendered only for a recommended profile", { timeout: TEST_TIMEOUT_MS }, () => {
  const h = providers();
  const badge = h.call("recommendedBadge", { kind: "soniox-realtime" });
  assert.match(badge, /recommended-badge/);
  assert.match(badge, /推荐/);
  assert.equal(h.call("recommendedBadge", { kind: "deepgram-streaming" }), "");
  assert.equal(h.call("recommendedBadge", { kind: "dashscope-livetranslate-realtime" }), "");
});

test("the role selector stars the profile itself, not the other bilingual one", { timeout: TEST_TIMEOUT_MS }, () => {
  // The catalog names carry no star, so the option text is where the mark has to
  // come from -- and it has to come from exactly one place.
  const h = providers();
  const soniox = h.call(
    "providerOption",
    { id: "soniox-stt-rt-v5", kind: "soniox-realtime", label: "Soniox STT RT v5（端到端双语）", model: "stt-rt-v5" },
    true,
  );
  const qwen = h.call(
    "providerOption",
    { id: "bailian-qwen38-livetranslate", kind: "dashscope-livetranslate-realtime", label: "百炼 Qwen3.8-LiveTranslate（端到端双语）", model: "qwen3.8-livetranslate-flash-realtime" },
    false,
  );
  assert.equal((soniox.match(/⭐/g) || []).length, 1, soniox);
  assert.match(soniox, /⭐ Soniox STT RT v5（端到端双语）/);
  assert.doesNotMatch(qwen, /⭐/, qwen);
  assert.match(qwen, /百炼 Qwen3\.8-LiveTranslate（端到端双语）/);
});

test("the protocol list stars Soniox and leaves the other bilingual protocol plain", { timeout: TEST_TIMEOUT_MS }, () => {
  // The dropdown is the one place the star lives in the text itself, so a second
  // star here is the second star the viewer would see.
  const source = require("node:fs").readFileSync(
    require("node:path").resolve(__dirname, "../web-player/player.js"),
    "utf8",
  );
  const starred = [...source.matchAll(/\["([a-z0-9-]+)", "[^"]*⭐/g)].map((match) => match[1]);
  assert.deepEqual(starred, ["soniox-realtime"]);
  const i18n = require("node:fs").readFileSync(
    require("node:path").resolve(__dirname, "../web-player/i18n.js"),
    "utf8",
  );
  assert.equal((i18n.match(/"⭐ /g) || []).length, 5, "one starred protocol name per locale");
  assert.match(i18n, /"proto\.soniox-realtime": "⭐/);
  assert.doesNotMatch(i18n, /"proto\.dashscope-livetranslate-realtime": "⭐/);
  assert.doesNotMatch(i18n, /"profile\.[a-z0-9-]+": "⭐/);
});

test("every ASR protocol the catalog can hold is offered in the settings dialog", { timeout: TEST_TIMEOUT_MS }, () => {
  // The kind list is what lets a user ADD the profile, so a registered kind
  // missing from it is a Provider that exists and cannot be configured.
  const h = providers();
  const source = require("node:fs").readFileSync(
    require("node:path").resolve(__dirname, "../web-player/player.js"),
    "utf8",
  );
  for (const kind of [
    "soniox-realtime",
    "dashscope-livetranslate-realtime",
    "dashscope-qwen-realtime",
    "deepgram-streaming",
  ]) {
    assert.match(source, new RegExp(`\\["${kind}"`), `${kind} is missing from providerKinds`);
  }
  // The order and the headings carry the recommendation: a protocol that has
  // never faced a real stream must not sit unmarked next to one that has.
  assert.match(
    source, /asr: \[\s*\[\s*"bilingual",/,
    "the end-to-end bilingual protocols must be the first group",
  );
  assert.match(source, /<optgroup label=/, "the list must be grouped, not one long flat select");
  assert.ok(
    /"recognition",\s*\[[\s\S]*?"dashscope-qwen-realtime"/.test(source),
    "recognition-only protocols belong to the second group",
  );
  assert.equal(typeof h.call("recommendedFirst", []).length, "number");
});
