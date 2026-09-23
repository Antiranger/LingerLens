/*
 * C3: the two options that had no consumer are gone, and old catalogs still work.
 *
 * `options.language` had an editor and no reader: the adapter builds its request
 * from the global SourceLanguagePolicy. `hotwordsEnabled` was written by the
 * builtin template and the shipped example and read by nothing: the real hotword
 * path keys off `vocabulary` / `vocabularyId`.
 *
 * Removing an editor is only safe if the save path does not rebuild the options
 * object from the editors it knows about. It does not -- handleProviderInput
 * writes the ONE key the event names -- and that is what the round-trip case here
 * pins, with the real handler rather than a model of it.
 */
const test = require("node:test");
const assert = require("node:assert/strict");

const { playerContext, spy, stubElement } = require("./player-harness.js");

const TEST_TIMEOUT_MS = 5000;

const OPENAI_OPTIONS = {
  language: "en",
  windowSeconds: 4,
  requestTimeoutSeconds: 9,
  someFutureKey: "kept",
};

function options() {
  const globals = {
    updateLabel: (key, fallback) => fallback || key,
    escapeHtml: (value) => String(value),
    checked: (value) => (value ? " checked" : ""),
    el: () => stubElement(),
    applyProviderKindDefaults: spy(),
    renderProviderProfiles: spy(),
  };
  const provider = { id: "prof", kind: "openai-audio-transcriptions", options: { ...OPENAI_OPTIONS } };
  globals.providerCatalog = { asr: { providers: [provider] }, translation: { providers: [] } };
  const context = playerContext({
    globals,
    functions: ["asrOptionFields", "translationModeField", "handleProviderInput"],
  });
  return { ...context, globals, provider };
}

function inputEvent(option, value, type = "number") {
  const card = stubElement();
  card.dataset = { providerId: "prof" };
  card.closest = (selector) => (selector === "#asrProfiles" ? {} : null);
  return {
    currentTarget: card,
    target: { dataset: { option }, type, value, checked: false },
  };
}

test(
  "the transcription profile no longer offers a language editor (D)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = options();
    const html = h.call("asrOptionFields", h.provider);

    assert.doesNotMatch(html, /data-option="language"/, "a control that changes nothing");
    // The options that DO reach the adapter must still be editable.
    assert.match(html, /data-option="windowSeconds"/);
    assert.match(html, /data-option="requestTimeoutSeconds"/);
    assert.match(html, /value="4"/, "the stored window value must still be shown");
    assert.match(html, /value="9"/, "the stored timeout value must still be shown");
  },
);

test(
  "the other profiles keep their own fields (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = options();
    const soniox = h.call("asrOptionFields", {
      kind: "soniox-realtime",
      options: { enableEndpointDetection: true },
    });
    assert.match(soniox, /data-option="enableEndpointDetection"/);
    const dashscope = h.call("asrOptionFields", {
      kind: "dashscope-task-asr",
      options: { vocabularyId: "vocab-1" },
    });
    assert.match(dashscope, /data-option="vocabularyId"/, "the real hotword control must stay");
    assert.doesNotMatch(dashscope, /hotwordsEnabled/);
  },
);

test(
  "the bilingual profiles expose their own translation controls (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = options();

    // Soniox: the built-in translation must be reachable from the UI, because an
    // option with no editor is an option nobody can turn on.
    const soniox = h.call("asrOptionFields", {
      kind: "soniox-realtime",
      options: { translationType: "one_way", enableEndpointDetection: true },
    });
    assert.match(soniox, /data-option="translationType"/);
    assert.match(soniox, /value="one_way" selected/, "the saved mode must be shown");
    assert.match(soniox, /data-option="translationLanguageA"/);
    assert.match(soniox, /opt\.translationHelp|这里控制 Soniox 是否/, "the mode must explain translation direction versus display mode");

    const live = h.call("asrOptionFields", {
      kind: "dashscope-livetranslate-realtime",
      options: { audioOutput: true, sourceLanguage: "ja", workspaceId: "llm-abc" },
    });
    assert.match(live, /data-option="audioOutput"/);
    assert.match(live, /data-option="sourceLanguage"/);
    assert.match(live, /value="ja"/);
    // The workspace id used to be a note telling the user to hand-edit the URL;
    // it is now the field the adapter fills into that placeholder, so the fix has
    // to be reachable rather than merely described.
    assert.match(live, /data-option="workspaceId"/);
    assert.match(live, /value="llm-abc"/, "the saved workspace id must be shown");
    assert.match(
      live, /3\.8/,
      "pinning a source language does nothing on 3.8, and the field has to say so",
    );
  },
);

test(
  "an empty translation mode is what 'off' looks like on the wire (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = options();
    const html = h.call("asrOptionFields", {
      kind: "soniox-realtime",
      options: { translationType: "off" },
    });
    // "off" is not a Soniox mode; it must render as the empty option rather
    // than as a selected value the backend would send verbatim.
    assert.doesNotMatch(html, /value="off"/);
    assert.match(html, /<option value="" selected>/);
  },
);

test(
  "editing one option preserves every legacy and unknown key (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = options();
    h.call("handleProviderInput", inputEvent("windowSeconds", "5"));

    assert.equal(h.provider.options.windowSeconds, 5, "the edited key must be written");
    assert.equal(h.provider.options.language, "en", "a legacy key must survive the save");
    assert.equal(
      h.provider.options.someFutureKey,
      "kept",
      "an option the UI never showed must survive the save",
    );
    assert.equal(h.globals.renderProviderProfiles.count(), 0, "a plain option edit re-renders nothing");
  },
);

test(
  "an unknown provider id writes nothing (G)",
  { timeout: TEST_TIMEOUT_MS },
  async () => {
    const h = options();
    const event = inputEvent("windowSeconds", "5");
    event.currentTarget.dataset = { providerId: "not-in-the-catalog" };
    h.call("handleProviderInput", event);

    assert.equal(h.provider.options.windowSeconds, 4, "a stray card must not edit another profile");
  },
);
