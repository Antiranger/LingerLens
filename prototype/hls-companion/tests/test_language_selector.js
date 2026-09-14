const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");

function loadApi() {
  const code = fs.readFileSync(path.join(root, "web-player/language-selector.js"), "utf8");
  const sandbox = {};
  vm.createContext(sandbox);
  new vm.Script(code, { filename: "language-selector.js" }).runInContext(sandbox);
  const api = sandbox.LagLingoLanguages;
  const catalog = JSON.parse(fs.readFileSync(path.join(root, "companion/data/languages.json"), "utf8"));
  api.setCatalog(catalog.languages);
  return api;
}

test("search matches English name, autonym, tag and aliases", () => {
  const api = loadApi();
  assert.ok(api.filterLanguages("japanese").includes("ja"));
  assert.ok(api.filterLanguages("JAPANESE").includes("ja"));
  assert.equal(api.filterLanguages("繁體")[0], "zh-Hant");
  assert.equal(api.filterLanguages("简体")[0], "zh-Hans");
  // Diacritic-insensitive autonym search (português).
  assert.ok(api.filterLanguages("portugues").includes("pt-BR"));
  // Tag and region/script distinctions stay searchable and distinct.
  assert.ok(api.filterLanguages("pt-b")[0] === "pt-BR");
  assert.ok(api.filterLanguages("sr-Latn").includes("sr-Latn"));
  assert.ok(api.filterLanguages("sr-Cyrl").includes("sr-Cyrl"));
  // Aliases resolve: typing zh-TW surfaces zh-Hant.
  assert.ok(api.filterLanguages("zh-TW").includes("zh-Hant"));
  // A UI-localized name also matches (Intl.DisplayNames when available).
  assert.ok(api.filterLanguages("日语", "zh-CN").includes("ja"));
  assert.equal(api.filterLanguages("not-a-language-zzz").length, 0);
});

test("empty query offers the complete catalog with common languages first", () => {
  const api = loadApi();
  const first = api.filterLanguages("");
  assert.equal(first.length, api.entries().length);
  assert.ok(first.length >= 99);
  assert.deepEqual(Array.from(first.slice(0, 5)), ["ja", "zh-Hans", "zh-Hant", "en", "ko"]);
});

test("direction comes from the catalog with a primary-subtag fallback", () => {
  const api = loadApi();
  assert.equal(api.directionFor("ar"), "rtl");
  assert.equal(api.directionFor("he"), "rtl");
  assert.equal(api.directionFor("fa"), "rtl");
  assert.equal(api.directionFor("ja"), "ltr");
  assert.equal(api.directionFor("en"), "ltr");
  // Not in the catalog: Hebrew primary subtag still resolves RTL.
  assert.equal(api.directionFor("he-IL"), "rtl");
  assert.equal(api.directionFor("tlh"), "ltr");
});

test("name parts expose localized primary, autonym and tag", () => {
  const api = loadApi();
  const parts = api.nameParts("zh-Hant");
  assert.equal(parts.tag, "zh-Hant");
  assert.equal(parts.autonym, "中文（繁體）");
  assert.ok(parts.primary.length > 0);
  assert.equal(api.nameParts("unknown-tag").primary, "unknown-tag");
});
