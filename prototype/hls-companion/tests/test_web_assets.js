const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");

test("Prototype 2 extension is MV3 with local cookie/native messaging permissions", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "extension/manifest.json"), "utf8"));
  assert.equal(manifest.manifest_version, 3);
  assert.ok(manifest.permissions.includes("cookies"));
  assert.ok(manifest.permissions.includes("nativeMessaging"));
  assert.ok(manifest.host_permissions.some((host) => host.includes("youtube.com")));
  assert.ok(manifest.host_permissions.some((host) => host.includes("bilibili.com")));
});

test("all prototype JavaScript parses", () => {
  for (const relative of ["extension/service-worker.js", "web-player/subtitle-scheduler.js", "web-player/player.js"]) {
    const code = fs.readFileSync(path.join(root, relative), "utf8");
    assert.doesNotThrow(() => new vm.Script(code, { filename: relative }));
  }
});

test("subtitle overlay is ready-gated, seq-polled, and wall-clock aligned", () => {
  const html = fs.readFileSync(path.join(root, "web-player/index.html"), "utf8");
  const css = fs.readFileSync(path.join(root, "web-player/style.css"), "utf8");
  const js = fs.readFileSync(path.join(root, "web-player/player.js"), "utf8");
  const scheduler = fs.readFileSync(path.join(root, "web-player/subtitle-scheduler.js"), "utf8");
  assert.match(html, /id="subtitleLayer"/);
  assert.match(html, /id="subtitleOffset"/);
  assert.match(html, /id="asrProvider"/);
  assert.match(html, /id="modelSettingsDialog"/);
  assert.match(html, /id="asrModel"/);
  assert.match(html, /Qwen3 与 Fun-ASR 使用不同的 WebSocket 协议/);
  assert.match(js, /providerId:\s*el\("asrModel"\)\.value/);
  assert.match(js, /dashscope-task-asr/);
  assert.match(html, /id="translationBaseUrl"/);
  assert.match(html, /id="translationModel"/);
  assert.match(html, /id="translationApiKey"/);
  assert.match(html, /id="cookieImportDialog"/);
  assert.match(html, /id="cookiePayload"/);
  assert.match(html, /id="openCookieImport"/);
  assert.match(html, /subtitle-scheduler\.js/);
  assert.match(html, /id="subtitleReadyLag"/);
  assert.match(html, /id="budgetMargin"/);
  assert.match(css, /\.subtitle-layer[^}]+pointer-events:\s*none/s);
  assert.match(css, /transition:\s*opacity\s+120ms/);
  assert.match(js, /hls\?\.playingDate/);
  assert.match(js, /\/api\/subtitles\?afterSeq=/);
  assert.match(js, /setInterval\(refreshSubtitles, 500\)/);
  assert.match(js, /setInterval\(renderSubtitle, 100\)/);
  assert.match(js, /subtitleScheduler\.pick/);
  assert.match(js, /\/api\/publish-delay/);
  assert.doesNotMatch(js, /textContent\s*=\s*["'`]翻译中/);
  assert.match(scheduler, /cue\.state === "done" \|\| cue\.state === "failed"/);
  // The display window is anchored at the sentence start, not its end.
  assert.match(scheduler, /Math\.max\(t, startOf\(cue\)\)/);
  assert.doesNotMatch(scheduler, /Math\.max\(t, cue\.tEnd\)/);
  assert.match(scheduler, /maxLateSeconds/);
  assert.match(scheduler, /minDwell/);
  assert.match(html, /id="cueDuration"/);
  assert.match(html, /id="schedulerDrops"/);
  assert.match(js, /subtitleScheduler\.stats/);
  assert.match(js, /translationLatency/);
  assert.match(js, /\/api\/model-settings/);
  assert.match(js, /\/api\/auth-cookies/);
  assert.match(js, /authToken = data\.authToken/);
});

test("cookie import is platform-aware without exposing cookie values", () => {
  const html = fs.readFileSync(path.join(root, "web-player/index.html"), "utf8");
  const js = fs.readFileSync(path.join(root, "web-player/player.js"), "utf8");
  assert.match(html, /id="cookiePlatform"/);
  assert.match(html, /value="youtube"/);
  assert.match(html, /value="bilibili"/);
  assert.match(js, /platform:\s*el\("cookiePlatform"\)\.value/);
  assert.match(js, /SESSDATA/);
  assert.match(js, /data\.names/);
  assert.doesNotMatch(js, /data\.cookies|data\.values/);
});

test("server status restores stop control after refresh or external start", () => {
  const js = fs.readFileSync(path.join(root, "web-player/player.js"), "utf8");
  assert.match(js, /el\("stop"\)\.disabled\s*=\s*!serverActive/);
  assert.match(js, /const serverActive\s*=\s*data\.state === "running"/);
  assert.doesNotMatch(js, /idle"\s*&&\s*data\.pageUrl/);
  assert.match(js, /resetStoppedUi/);
});

test("hls.js is bundled locally and no CDN is referenced", () => {
  const html = fs.readFileSync(path.join(root, "web-player/index.html"), "utf8");
  const vendor = path.join(root, "web-player/vendor/hls.min.js");
  assert.match(html, /\/vendor\/hls\.min\.js/);
  assert.doesNotMatch(html, /<(script|link)[^>]+https?:\/\//i);
  assert.ok(fs.statSync(vendor).size > 100_000);
});
