/*
 * Which language the player opens in, and whether it remembers.
 *
 * Before 2026-09-22 the switcher was write-only: the page always booted Chinese,
 * a non-Chinese user had to re-pick on every launch, and the choice was thrown
 * away on reload. This compiles i18n.js in a vm with a DOM that answers nothing
 * (querySelector -> null), which is enough to exercise the selection path: the
 * rendering of a real page is test_browser_metrics.py's job.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "web-player/i18n.js"), "utf8");

function boot({ languages = ["zh-CN"], stored = null } = {}) {
  const store = new Map(stored === null ? [] : [["lingerlens.locale", stored]]);
  const storage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, value),
  };
  const switcher = {
    value: "zh-CN",
    change: null,
    addEventListener(type, handler) { if (type === "change") this.change = handler; },
  };
  const document = {
    documentElement: { lang: "zh-CN" },
    querySelector: () => null,
    getElementById: (id) => (id === "localeSwitch" ? switcher : null),
    createTextNode: (text) => ({ nodeType: 3, nodeValue: text }),
    addEventListener() {},
    dispatchEvent() {},
  };
  const context = {
    document,
    navigator: { languages, language: languages[0] },
    localStorage: storage,
    console,
    CustomEvent: class { constructor(type, init) { Object.assign(this, { type }, init); } },
    MutationObserver: class { observe() {} disconnect() {} },
  };
  context.window = { localStorage: storage };
  vm.createContext(context);
  vm.runInContext(source, context);
  return { i18n: context.window.I18N, switcher, store };
}

test("with nothing chosen yet, the player opens in the language the browser speaks", () => {
  assert.equal(boot({ languages: ["en-US", "zh-CN"] }).i18n.current, "en");
  assert.equal(boot({ languages: ["ja-JP"] }).i18n.current, "ja");
  assert.equal(boot({ languages: ["de-DE", "en-US"] }).i18n.current, "de");
  assert.equal(boot({ languages: ["zh-CN"] }).i18n.current, "zh-CN");
  // A language the tables do not carry must not half-localize the page.
  assert.equal(boot({ languages: ["fr-FR"] }).i18n.current, "zh-CN");
  // Chinese regional variants land on the interface's own default, not on English.
  assert.equal(boot({ languages: ["zh-TW"] }).i18n.current, "zh-CN");
});

test("an explicit choice beats the browser and survives a reload", () => {
  const remembered = boot({ languages: ["en-US"], stored: "de" });
  assert.equal(remembered.i18n.current, "de", "the saved language wins over the browser");
  assert.equal(remembered.switcher.value, "de", "the switcher shows what the page is actually in");

  const junk = boot({ languages: ["ja-JP"], stored: "klingon" });
  assert.equal(junk.i18n.current, "ja", "an unreadable stored value falls back to the browser");
});

test("picking a language localizes now and is remembered for next time", () => {
  const { switcher, store, i18n } = boot({ languages: ["zh-CN"] });
  assert.equal(i18n.current, "zh-CN");
  assert.equal(store.has("lingerlens.locale"), false, "booting must not claim the user chose");

  switcher.change({ target: { value: "en" } });
  assert.equal(i18n.current, "en");
  assert.equal(store.get("lingerlens.locale"), "en");
});

test("a catalog name resolves in the interface language, and a typed one does not move", () => {
  const english = boot({ languages: ["en-US"] }).i18n;
  const chinese = boot({ languages: ["zh-CN"] }).i18n;
  // The settings list asks by derived key with the backend's Chinese as fallback.
  assert.equal(
    english.t("profile.soniox-stt-rt-v5", null, "Soniox STT RT v5（端到端双语）"),
    "Soniox STT RT v5 (end-to-end bilingual)",
  );
  assert.equal(
    chinese.t("profile.soniox-stt-rt-v5", null, "Soniox STT RT v5（端到端双语）"),
    "Soniox STT RT v5（端到端双语）",
  );
  // A profile the viewer named themselves has no key: their wording wins verbatim.
  assert.equal(
    english.t("profile.asr-1", null, "我的嘉然专用通道"),
    "我的嘉然专用通道",
  );
});

/*
 * The shipped profileText() against the real tables. Resolving a name by id is
 * only safe while the record still carries the name the app wrote: the profiles
 * in use on a real machine are renamed records whose ids happen to be built-in
 * ones, and translating those back would rename a profile on purpose.
 */
test("a renamed profile keeps its own name in any language", () => {
  const { extractFunction } = require("./player-harness.js");
  const english = boot({ languages: ["en-US"] }).i18n;
  const shipped = english.catalogName("soniox-stt-rt-v5");
  assert.equal(shipped, "Soniox STT RT v5（端到端双语）", "catalogName hands back the Chinese copy");
  assert.equal(english.catalogName("asr-1"), "", "a hand-made profile is not in the catalog");

  const context = {
    window: { I18N: english },
    updateLabel: (key, fallback) => english.t(key, null, fallback),
  };
  vm.createContext(context);
  const player = fs.readFileSync(path.join(root, "web-player/player.js"), "utf8");
  vm.runInContext(extractFunction(player, "profileText"), context);

  assert.equal(
    context.profileText("soniox-stt-rt-v5", shipped),
    "Soniox STT RT v5 (end-to-end bilingual)",
  );
  assert.equal(
    context.profileText("bailian-fun-asr-2026-02-28", "Soniox"),
    "Soniox",
    "that record was renamed by hand; the catalog does not get to reclaim it",
  );
  assert.equal(
    context.profileText("asr-1", "Qwen 3.8 live translate"),
    "Qwen 3.8 live translate",
  );
});
