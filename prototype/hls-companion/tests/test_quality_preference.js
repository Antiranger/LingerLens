const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pickPreferredQuality } = require(path.resolve(__dirname, "../web-player/quality-preference.js"));

const q = (id, height, separateAudio, extra = {}) => ({
  qualityId: id,
  height,
  separateAudio,
  requiresTranscode: false,
  ...extra,
});

test("prefers separate v+a legs over muxed at the same height", () => {
  const qualities = [q("1080-muxed", 1080, false), q("1080-sep", 1080, true), q("720-sep", 720, true)];
  assert.equal(pickPreferredQuality(qualities).qualityId, "1080-sep");
});

test("higher muxed tier still beats a lower separate tier", () => {
  const qualities = [q("1080-muxed", 1080, false), q("720-sep", 720, true)];
  assert.equal(pickPreferredQuality(qualities).qualityId, "1080-muxed");
});

test("falls back to 720p when no 1080p exists", () => {
  const qualities = [q("720-muxed", 720, false), q("360-sep", 360, true)];
  assert.equal(pickPreferredQuality(qualities).qualityId, "720-muxed");
});

test("skips transcode-only options", () => {
  const qualities = [q("1080-sep", 1080, true, { requiresTranscode: true }), q("720-sep", 720, true)];
  assert.equal(pickPreferredQuality(qualities).qualityId, "720-sep");
});

test("never auto-picks above 1080p", () => {
  const qualities = [q("2160-sep", 2160, true)];
  assert.equal(pickPreferredQuality(qualities), null);
});

test("returns null for empty or missing lists", () => {
  assert.equal(pickPreferredQuality([]), null);
  assert.equal(pickPreferredQuality(undefined), null);
});

test("keeps server order within a tier", () => {
  const qualities = [q("1080-sep-60", 1080, true), q("1080-sep-30", 1080, true)];
  assert.equal(pickPreferredQuality(qualities).qualityId, "1080-sep-60");
});
