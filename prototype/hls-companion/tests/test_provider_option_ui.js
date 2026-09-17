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
    functions: ["asrOptionFields", "handleProviderInput"],
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
