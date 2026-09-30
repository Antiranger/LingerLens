const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const { playerSource, extractFunction, stubElement, spy } = require("./player-harness.js");

function picker(provider) {
  const source = playerSource();
  const start = source.indexOf("  const providerModels = {");
  assert.ok(start >= 0);
  const end = source.indexOf("\n  };", start) + 5;
  const context = {
    providerCatalog: { asr: { providers: [provider] }, translation: { providers: [] } },
    updateLabel: (key, fallback) => fallback,
    escapeHtml: value => String(value),
    renderProviderProfiles: spy(),
  };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end) + "\n" +
    ["providerModelChoices", "applyProviderModel", "handleProviderInput"].map(name => extractFunction(source, name)).join("\n"), context);
  return context;
}

test("model selection fills the actual Tencent engine and keeps credentials and custom options", () => {
  const provider = { id: "my-asr", kind: "tencent-asr", model: "16k_ja", label: "My name",
    apiKey: "fixture-key", baseUrl: "wss://fixture.invalid", options: { engineModelType: "16k_ja", appId: "fixture-app", custom: true } };
  const h = picker(provider);
  const card = stubElement();
  card.dataset.providerId = provider.id;
  card.closest = () => ({});
  const event = { type: "change", currentTarget: card,
    target: { value: "16k_en", hasAttribute: key => key === "data-model-preset" } };
  h.handleProviderInput(event);
  assert.equal(provider.model, "16k_en");
  assert.equal(provider.options.engineModelType, "16k_en");
  assert.equal(provider.options.appId, "fixture-app");
  assert.equal(provider.options.custom, true);
  assert.equal(provider.apiKey, "fixture-key");
  assert.equal(provider.label, "My name");
  assert.equal(provider.baseUrl, "wss://fixture.invalid");
  assert.equal(h.renderProviderProfiles.count(), 1);
});

test("custom model IDs remain editable and are not silently replaced by recommendations", () => {
  const provider = { kind: "soniox-realtime", model: "custom-model" };
  const h = picker(provider);
  assert.match(h.providerModelChoices(provider), /value="" selected/);
  assert.match(h.providerModelChoices(provider), /stt-rt-v5/);
  h.applyProviderModel(provider, "unknown-preset");
  assert.equal(provider.model, "custom-model");
  h.applyProviderModel(provider, "stt-rt-v5");
  assert.equal(provider.model, "stt-rt-v5");
  assert.match(h.providerModelChoices(provider), /value="stt-rt-v5" selected/);
  assert.equal(h.providerModelChoices({ kind: "custom-gateway" }), "");
});

test("all ASR protocols expose recommendations and bilingual Qwen starts with the timing preset", () => {
  const h = picker({});
  for (const kind of ["soniox-realtime", "soniox-realtime-transcribe", "dashscope-livetranslate-realtime",
    "dashscope-qwen-realtime", "dashscope-task-asr", "openai-audio-transcriptions", "openai-realtime-transcription",
    "deepgram-streaming", "assemblyai-streaming", "volcengine-sauc", "elevenlabs-scribe-realtime",
    "speechmatics-realtime", "tencent-asr"]) {
    assert.match(h.providerModelChoices({ kind }), /<option value="[^"\s]+"/, kind);
  }
  const qwen = h.providerModelChoices({ kind: "dashscope-livetranslate-realtime" });
  assert.match(qwen, /qwen3\.5-livetranslate-flash-realtime/);
  assert.doesNotMatch(qwen, /qwen3\.8/);
});

test("the shipped editor includes both the recommendation selector and manual model input", () => {
  const renderer = extractFunction(playerSource(), "renderProviderSection");
  assert.match(renderer, /providerModelChoices\(provider\)/);
  assert.match(renderer, /data-field="model"/);
  const source = require("node:fs").readFileSync(require("node:path").join(__dirname, "../web-player/i18n.js"), "utf8");
  assert.match(source, /"proto\.dashscope-qwen-realtime": "千问"/);
  assert.match(source, /"proto\.tencent-asr": "腾讯"/);
  assert.match(source, /"proto\.google-genai": "谷歌"/);
  assert.match(source, /"proto\.volcengine-sauc": "豆包"/);
  const defaults = playerSource().match(/"soniox-realtime": \{ model: "stt-rt-v5"[^\n]+/)[0];
  assert.match(defaults, /nativeTranslationFallback: false/,
    "the first bilingual profile must not require an unconfigured translator");
});
