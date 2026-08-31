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
  for (const relative of ["extension/service-worker.js", "web-player/subtitle-scheduler.js", "web-player/subtitle-window-controller.js", "web-player/player.js"]) {
    const code = fs.readFileSync(path.join(root, relative), "utf8");
    assert.doesNotThrow(() => new vm.Script(code, { filename: relative }));
  }
});

test("subtitle overlay is ready-gated, seq-polled, and wall-clock aligned", () => {
  const html = fs.readFileSync(path.join(root, "web-player/index.html"), "utf8");
  const css = fs.readFileSync(path.join(root, "web-player/style.css"), "utf8");
  const js = fs.readFileSync(path.join(root, "web-player/player.js"), "utf8");
  const scheduler = fs.readFileSync(path.join(root, "web-player/subtitle-scheduler.js"), "utf8");
  const windowController = fs.readFileSync(path.join(root, "web-player/subtitle-window-controller.js"), "utf8");
  assert.match(html, /id="subtitleLayer"/);
  assert.match(html, /id="subtitleDragHandle"/);
  assert.match(html, /id="toggleFullscreen"/);
  assert.match(html, /id="resetSubtitlePosition"/);
  assert.match(html, /id="subtitleOpacity"/);
  assert.match(html, /id="subtitleScale"/);
  assert.match(html, /id="subtitleSourceColor"/);
  assert.match(html, /id="subtitleTranslationColor"/);
  assert.match(html, /id="subtitleOffset"/);
  assert.doesNotMatch(html, /id="asrProvider"/);
  assert.doesNotMatch(html, /id="translationProvider"/);
  assert.match(html, /id="modelSettingsDialog"/);
  assert.match(html, /id="asrProfiles"/);
  assert.match(html, /id="translationProfiles"/);
  assert.match(html, /id="addAsrProfile"/);
  assert.match(html, /id="addTranslationProfile"/);
  assert.match(js, /renderProviderProfiles/);
  assert.match(js, /openai-audio-transcriptions/);
  assert.match(js, /data-action="delete"/);
  assert.doesNotMatch(js, /asrProviderId\s*:/);
  assert.doesNotMatch(js, /translationProviderId\s*:/);
  assert.match(js, /data-field="baseUrl"/);
  assert.match(js, /data-field="model"/);
  assert.match(js, /data-field="apiKey"/);
  assert.match(js, /data-field="pricePerSecondCny"/);
  assert.match(js, /data-field="pricePerMillionInputTokensCny"/);
  assert.match(js, /data-field="pricePerMillionCachedInputTokensCny"/);
  assert.match(js, /data-field="pricePerMillionOutputTokensCny"/);
  assert.match(js, /type="text" value=.*provider\.apiKey/);
  assert.match(js, /isActive \|\| group\.providers\.length === 1/);
  assert.match(html, /id="cookieImportDialog"/);
  assert.match(html, /id="cookiePayload"/);
  assert.match(html, /id="openCookieImport"/);
  assert.match(html, /subtitle-scheduler\.js/);
  assert.match(html, /id="subtitleReadyLag"/);
  assert.match(html, /id="budgetMargin"/);
  assert.match(css, /\.subtitle-layer[^}]+pointer-events:\s*none/s);
  assert.match(css, /\.subtitle-drag-handle[^}]+pointer-events:\s*auto/s);
  assert.match(css, /transition:\s*opacity\s+120ms/);
  assert.match(windowController, /requestFullscreen/);
  assert.match(js, /fullscreenchange/);
  assert.match(js, /ResizeObserver/);
  assert.match(html, /subtitle-window-controller\.js/);
  assert.match(js, /hls\?\.playingDate/);
  assert.match(js, /\/api\/subtitles\?afterSeq=/);
  assert.match(js, /setInterval\(refreshSubtitles, 500\)/);
  assert.match(js, /setInterval\(renderSubtitle, 100\)/);
  assert.match(js, /subtitleScheduler\.pick/);
  assert.match(js, /\/api\/target-delay/);
  assert.doesNotMatch(html, /下载后额外延迟/);
  assert.doesNotMatch(html, /id="publishDelay"/);
  assert.match(html, /id="targetDelay"[^>]+type="number"[^>]+min="11"[^>]+value="15"/);
  assert.match(html, /希望落后真实直播的总时间/);
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
  assert.match(html, /id="asrUsageCost"/);
  assert.match(html, /id="translationUsageCost"/);
  assert.match(html, /id="totalUsageCost"/);
  assert.match(js, /asrEstimatedCostCny/);
  assert.match(js, /translationEstimatedCostCny/);
  assert.match(js, /totalEstimatedCostCny/);
  assert.match(js, /toLocaleString/);
  assert.match(js, /不可估算/);
  assert.doesNotMatch(js, /Number\(subtitles\.estimatedCostCny \|\| 0\)/);
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
