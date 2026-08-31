const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const root = path.resolve(__dirname, "..");
const extension = path.join(root, "extension");

function loadMetrics() {
  const context = vm.createContext({ window: {} });
  const source = fs.readFileSync(path.join(extension, "src", "metrics.js"), "utf8");
  new vm.Script(source, { filename: "metrics.js" }).runInContext(context);
  return context.window.LiveDelaySpike.metrics;
}

test("manifest is MV3 and remains scoped to the two spike platforms", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(extension, "manifest.json"), "utf8"));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, undefined);
  assert.deepEqual(manifest.host_permissions, [
    "https://www.youtube.com/*",
    "https://live.bilibili.com/*"
  ]);
  assert.equal((manifest.permissions || []).includes("tabCapture"), false);
  assert.equal(JSON.stringify(manifest).includes("offscreen"), false);
});

test("all extension scripts parse as JavaScript", () => {
  for (const file of ["adapters.js", "metrics.js", "content.js"]) {
    const source = fs.readFileSync(path.join(extension, "src", file), "utf8");
    assert.doesNotThrow(() => new vm.Script(source, { filename: file }));
  }
});

test("summary computes delay distribution and stable-band metrics", () => {
  const { summarize } = loadMetrics();
  const samples = [4, 5, 5, 6, 8].map((actualDelay) => ({ actualDelay }));
  const result = summarize(samples, 5);
  assert.equal(result.sampleCount, 5);
  assert.equal(result.validDelaySampleCount, 5);
  assert.equal(result.actualDelayP50, 5);
  assert.equal(result.actualDelayP95, 7.6);
  assert.equal(result.meanAbsoluteError, 1);
  assert.equal(result.targetBandRatio, 0.8);
  assert.equal(result.longestStableSeconds, 4);
});

test("summary ignores samples without a finite actual delay", () => {
  const { summarize } = loadMetrics();
  const result = summarize([{ actualDelay: null }, { actualDelay: Number.NaN }], 10);
  assert.equal(result.validDelaySampleCount, 0);
  assert.equal(result.actualDelayP50, null);
  assert.equal(result.meanAbsoluteError, null);
  assert.equal(result.targetBandRatio, null);
});

test("CSV exporter preserves nested seekable ranges and escapes URLs", () => {
  const { samplesToCsv } = loadMetrics();
  const csv = samplesToCsv([{
    timestamp: "2026-01-01T00:00:00.000Z",
    url: "https://example.test/watch?a=1,b=2",
    seekableRanges: [{ start: 0, end: 20 }]
  }]);
  assert.match(csv, /^timestamp,url,seekableRanges\n/);
  assert.match(csv, /"https:\/\/example\.test\/watch\?a=1,b=2"/);
  assert.match(csv, /"\[\{""start"":0,""end"":20\}\]"/);
});
