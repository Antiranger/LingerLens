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
  assert.ok(manifest.host_permissions.some((host) => host.includes("twitch.tv")));
});

test("all prototype JavaScript parses", () => {
  for (const relative of [
    "extension/service-worker.js",
    "web-player/ui-bootstrap.js",
    "web-player/media-clock.js",
    "web-player/poll-loop.js",
    "web-player/quality-preference.js",
    "web-player/workbench-controller.js",
    "web-player/pane-resizer.js",
    "web-player/live-messages-client.js",
    "web-player/playback-recovery.js",
    "web-player/diagnostics-log.js",
    "web-player/i18n.js",
    "web-player/control-bar.js",
    "web-player/language-selector.js",
    "web-player/subtitle-scheduler.js",
    "web-player/subtitle-window-controller.js",
    "web-player/player.js",
  ]) {
    const code = fs.readFileSync(path.join(root, relative), "utf8");
    assert.doesNotThrow(() => new vm.Script(code, { filename: relative }));
  }
});

test("language UI is searchable, generic and bidi-safe", () => {
  const html = fs.readFileSync(path.join(root, "web-player/index.html"), "utf8");
  const css = fs.readFileSync(path.join(root, "web-player/style.css"), "utf8");
  const js = fs.readFileSync(path.join(root, "web-player/player.js"), "utf8");
  const selector = fs.readFileSync(path.join(root, "web-player/language-selector.js"), "utf8");
  // Generic display modes: not bound to specific languages.
  assert.match(html, /原文\+译文/);
  assert.match(html, /仅译文/);
  assert.match(html, /仅原文/);
  assert.doesNotMatch(html, /中日双语/);
  assert.doesNotMatch(html, /仅中文/);
  // Source policy controls, detect candidates and searchable selectors.
  assert.match(html, /id="sourceLanguageMode"/);
  assert.match(html, /指定语言/);
  assert.match(html, /自动识别/);
  assert.match(html, /id="sourceLanguage" class="language-selector"/);
  assert.match(html, /class="language-native-fallback" aria-label="指定源语言"/);
  assert.match(html, /class="language-native-fallback" aria-label="目标语言"/);
  assert.match(html, /日语 · 日本語 · ja/);
  assert.match(html, /简体中文 · 中文（简体） · zh-Hans/);
  assert.match(html, /id="sourceCandidates" class="language-selector"/);
  assert.match(html, /id="sourceCandidateChips"/);
  assert.match(html, /id="allowCodeSwitching"/);
  assert.match(html, /id="targetLanguage" class="language-selector"/);
  assert.doesNotMatch(html, /id="languageCapabilityHint"/);
  assert.match(html, /language-selector\.js\?v=language-ui-2/);
  assert.match(html, /player\.js\?v=language-ui-2/);
  assert.match(html, /style\.css\?v=language-ui-2/);
  // The old fixed two-item target select is gone; the native fallback lives
  // inside the searchable selector host and is replaced when JS initializes.
  assert.doesNotMatch(html, /<select id="targetLanguage"/);
  // Keyboard-accessible combobox contract.
  assert.match(selector, /role", "combobox"/);
  assert.match(selector, /role", "listbox"/);
  assert.match(selector, /role", "option"/);
  assert.match(selector, /aria-activedescendant/);
  assert.match(selector, /ArrowDown/);
  assert.match(selector, /ArrowUp/);
  assert.match(selector, /Enter/);
  assert.match(selector, /Escape/);
  assert.match(selector, /open\(\{ showAll: true \}\)/);
  assert.match(selector, /resultLimit = Number\.isFinite\(limit\).*catalog\.length/);
  // Search covers name, autonym and tag.
  assert.match(selector, /englishName/);
  assert.match(selector, /autonym/);
  assert.match(selector, /aliases/);
  // Start carries the policy object and the canonical target.
  assert.match(js, /sourcePolicyFromUi/);
  assert.match(js, /\/api\/languages/);
  assert.match(js, /validateLanguageSettingsClient/);
  assert.match(js, /persistLanguageSettings/);
  assert.match(js, /allowCodeSwitching/);
  assert.doesNotMatch(js, /sourceLanguage: "ja"/);
  // Bidi: each line gets dir=auto plus the catalog-direction fallback hook.
  assert.match(js, /zhLine\.dir = "auto"/);
  assert.match(js, /srcLine\.dir = "auto"/);
  assert.match(js, /dataset\.direction/);
  assert.match(css, /\.subtitle-zh,\s*\n?\.subtitle-src \{ unicode-bidi: isolate; \}/);
  assert.match(css, /unicode-bidi:\s*isolate/);
  assert.match(css, /\.subtitle-cue-row/);
  for (let index = 0; index < 10; index += 1) {
    assert.match(css, new RegExp(`data-speaker-color="${index}"`));
  }
  assert.match(css, /\.language-field\[hidden\]\s*\{\s*display:\s*none\s*!important/);
  assert.match(selector, /lang-combo/);
});

test("subtitle overlay is ready-gated, seq-polled, and wall-clock aligned", () => {
  const html = fs.readFileSync(path.join(root, "web-player/index.html"), "utf8");
  const css = fs.readFileSync(path.join(root, "web-player/style.css"), "utf8");
  const js = fs.readFileSync(path.join(root, "web-player/player.js"), "utf8");
  const scheduler = fs.readFileSync(path.join(root, "web-player/subtitle-scheduler.js"), "utf8");
  const windowController = fs.readFileSync(path.join(root, "web-player/subtitle-window-controller.js"), "utf8");
  assert.match(html, /id="subtitleLayer"/);
  assert.match(html, /class="subtitle-content"><\/div>/);
  assert.match(html, /id="subtitleResizeHandle"/);
  assert.match(html, /id="toggleFullscreen"/);
  assert.match(html, /id="resetSubtitlePosition"/);
  assert.match(html, /id="subtitleOpacity"/);
  assert.doesNotMatch(html, /id="subtitleScale"/);
  assert.match(html, /id="subtitleSourceColor"/);
  assert.match(html, /id="subtitleTranslationColor"/);
  assert.match(html, /id="subtitleOffset"/);
  assert.doesNotMatch(html, /id="asrProvider"/);
  assert.doesNotMatch(html, /id="translationProvider"/);
  assert.match(html, /id="modelSettingsDialog"/);
  assert.match(html, />模型设置</);
  assert.match(html, />语音识别配置</);
  assert.match(html, />翻译配置</);
  assert.doesNotMatch(html, /模型连接设置|语音识别连接|翻译连接|管理连接/);
  assert.match(html, /id="asrProfiles"/);
  assert.match(html, /id="translationProfiles"/);
  assert.match(html, /id="roleFallback"/);
  assert.match(js, /<option value="">不选<\/option>/);
  assert.match(js, /translationFallback/);
  assert.match(js, /fallback: value \? \[value\] : \[\]/);
  assert.doesNotMatch(html, /translationFallbackList|fallback-settings/);
  assert.doesNotMatch(js, /renderTranslationFallbackSelector|fallbackToggle|fallbackMove/);
  assert.doesNotMatch(html, /brand-jp|side-note|遅延ライブ翻訳|ラグリンゴ|メディアはローカルのみ/);
  assert.match(html, /id="addAsrProfile"/);
  assert.match(html, /id="addTranslationProfile"/);
  assert.match(js, /renderProviderProfiles/);
  assert.match(js, /openai-audio-transcriptions/);
  assert.match(js, /deepgram-streaming/);
  assert.match(js, /soniox-realtime/);
  assert.match(js, /openai-realtime-transcription/);
  assert.match(js, /assemblyai-streaming/);
  assert.match(js, /volcengine-sauc/);
  assert.match(js, /elevenlabs-scribe-realtime/);
  assert.match(js, /speechmatics-realtime/);
  assert.match(js, /tencent-asr/);
  assert.match(js, /data-option="enableSpeakerDiarization"/);
  assert.match(js, /data-option="resourceId"/);
  assert.match(js, /data-option="appId"/);
  assert.match(js, /data-action="delete"/);
  assert.doesNotMatch(js, /asrProviderId\s*:/);
  assert.doesNotMatch(js, /translationProviderId\s*:/);
  assert.match(js, /data-field="baseUrl"/);
  assert.match(js, /data-field="model"/);
  assert.match(js, /providerDefaults/);
  assert.match(js, /wss:\/\/stt-rt\.soniox\.com\/transcribe-websocket/);
  assert.match(js, /wss:\/\/api\.deepgram\.com\/v1\/listen/);
  assert.match(js, /https:\/\/api\.openai\.com\/v1/);
  assert.match(js, /asrProviderLabel/);
  assert.match(js, /translationProviderLabel/);
  assert.doesNotMatch(js, /\["qwen-mt",\s*"Qwen MT"\]/);
  assert.doesNotMatch(js, /model:\s*asr\s*\?\s*"whisper-1"/);
  assert.doesNotMatch(js, /127\.0\.0\.1:8000\/v1/);
  assert.match(js, /data-field="apiKey"/);
  assert.match(js, /data-field="pricePerSecondCny"/);
  assert.match(js, /data-field="pricePerMillionInputTokensCny"/);
  assert.match(js, /data-field="pricePerMillionCachedInputTokensCny"/);
  assert.match(js, /data-field="pricePerMillionOutputTokensCny"/);
  assert.match(js, /type="password" value=.*provider\.apiKey/);
  assert.match(js, /const cannotDelete = isActive[^;]+chatTranslation[^;]+group\.providers\.length === 1/);
  assert.doesNotMatch(html, /Qwen-MT/);
  assert.match(html, /id="cookieImportDialog"/);
  assert.match(html, /id="cookiePayload"/);
  assert.match(html, /id="openCookieImport"/);
  assert.match(html, /ui-bootstrap\.js\?v=language-ui-2/);
  const bootstrap = fs.readFileSync(path.join(root, "web-player/ui-bootstrap.js"), "utf8");
  assert.match(bootstrap, /openCookieImport/);
  assert.match(bootstrap, /openModelSettings/);
  assert.match(bootstrap, /showModal/);
  assert.match(html, /subtitle-scheduler\.js/);
  assert.match(html, /id="subtitleReadyLag"/);
  assert.match(html, /id="budgetMargin"/);
  assert.match(css, /input\[type="checkbox"\], input\[type="radio"\]/);
  assert.match(css, /width:\s*15px/);
  assert.match(css, /\.provider-fields > label:has\(input\[type="checkbox"\]\)/);
  assert.match(css, /\.subtitle-layer[^}]*\{[^}]*pointer-events:\s*auto/s);
  assert.match(css, /\.subtitle-resize-handle[^}]+cursor:\s*nwse-resize/s);
  assert.match(css, /transition:\s*opacity\s+120ms/);
  assert.match(windowController, /requestFullscreen/);
  assert.match(js, /fullscreenchange/);
  assert.match(js, /ResizeObserver/);
  assert.match(html, /subtitle-window-controller\.js/);
  assert.match(js, /mediaClock\?\.playingWallTime/);
  const mediaClock = fs.readFileSync(path.join(root, "web-player/media-clock.js"), "utf8");
  assert.doesNotMatch(mediaClock.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, ""), /Date\.now\(/);
  assert.match(js, /\/api\/subtitles\?afterSeq=/);
  assert.match(js, /run:\s*refreshSubtitles,\s*\n?\s*intervalMs:\s*500/);
  assert.match(js, /subtitleRenderTimer\s*=\s*setInterval\([\s\S]*?renderSubtitle\(\)[\s\S]*?,\s*100\)/);
  assert.match(js, /subtitleScheduler\.active/);
  assert.match(js, /return hash % 10/);
  assert.match(js, /row\.dataset\.speakerColor/);
  assert.match(js, /\/api\/target-delay/);
  assert.doesNotMatch(html, /下载后额外延迟/);
  assert.doesNotMatch(html, /id="publishDelay"/);
  assert.match(html, /id="targetDelay"[^>]+type="number"[^>]+min="11"[^>]+value="15"/);
  assert.match(html, /目标延迟/);
  assert.match(html, /当前实测延迟/);
  assert.match(html, /不包含直播源本身的延迟/);
  assert.doesNotMatch(js, /textContent\s*=\s*["'`]翻译中/);
  // Assert the live rule, not a comment. This previously matched
  // `cue.state === "done" || cue.state === "failed"`, which appears ONLY in a
  // comment at subtitle-scheduler.js:30 that says the opposite of the code at
  // :57 -- so the check passed no matter what displayable() did.
  const schedulerCode = scheduler.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, "");
  assert.match(
    schedulerCode,
    /function displayable\(cue\)\s*\{[\s\S]*?cue\.state === "done"[\s\S]*?\}/
  );
  assert.doesNotMatch(
    schedulerCode,
    /cue\.state === "failed"/,
    "a failed cue has no translation and must never be rendered"
  );
  // The display window is anchored at the sentence start, not its end.
  assert.match(scheduler, /Math\.max\(t, startOf\(cue\)\)/);
  assert.doesNotMatch(scheduler, /Math\.max\(t, cue\.tEnd\)/);
  assert.match(scheduler, /maxLateSeconds/);
  assert.match(scheduler, /minDwell/);
  assert.match(html, /id="cueDuration"/);
  assert.match(html, /id="schedulerDrops"/);
  assert.match(js, /subtitleScheduler\.stats/);
  assert.match(js, /translationLatency/);
  // A dead/error session must not leave the media-loading veil visible, and
  // playback recovery must trust the source-health classification instead of
  // treating every publisher-only packaging pause as a download outage.
  assert.match(js, /if \(data\.state === "error"\) \{[\s\S]*?setMediaLoading\(false\);/);
  assert.match(js, /health\.kind === "upstream"/);
  // The stall text must not promise a recovery the app never attempts: nothing
  // anywhere consumes RecoveryPolicy.action, so "attempting to reconnect" was a
  // claim with no mechanism behind it.
  assert.match(js, /直播源暂时没有新数据，正在等待恢复/);
  assert.doesNotMatch(js, /正在尝试重新连接/);
  assert.doesNotMatch(js, /正在自动恢复/);
  assert.doesNotMatch(js, /上游直播数据中断，正在自动恢复/);
  // The banner reports what the viewer is experiencing, never what the source is
  // doing. A source that goes quiet behind a deep buffer is invisible to the
  // viewer, and announcing it was the false alarm that fired on 8 of 101 samples
  // of a provably healthy stream.
  assert.match(js, /const starving = ahead < STALL_VISIBLE_BUFFER_SECONDS;/);
  assert.match(js, /if \(!stalled \|\| !starving\) \{/);
  // ...and the automatic pause must not outvote the play button, which it did on
  // every 1s status poll while the resume condition stayed out of reach.
  assert.match(js, /stallAutoPauseSuppressed = true;/);
  assert.match(js, /video\.addEventListener\("play"/);
  // A resume the viewer can wait for: the buffer is the only thing that makes
  // playing possible, so a deep buffer must release the automatic pause even
  // while the source is still stalled. Requiring a healthy source as well left
  // the player holding a pause on a full 30s buffer with the picture frozen --
  // observed live, not theorized.
  assert.match(js, /if \(ahead >= STALL_RESUME_BUFFER_SECONDS\) \{/);
  assert.doesNotMatch(js, /!stalled && ahead >= STALL_RESUME_BUFFER_SECONDS/);
  assert.match(js, /sessionAction === "stopping" \|\| lastSessionState !== "running"/);
  assert.match(html, /id="asrUsageCost"/);
  assert.match(html, /id="translationUsageCost"/);
  assert.match(html, /id="totalUsageCost"/);
  assert.match(js, /asrEstimatedCostCny/);
  // 费用按币种分组显示：一次会话可能同时用过人民币与美元的 Provider，不同
  // 币种不能相加。断言的是读取分组与币种，而不是某个单一的「合计」字段。
  assert.match(js, /costsByCurrency/);
  assert.match(js, /asrCostCurrency/);
  assert.match(js, /CURRENCY_SYMBOLS/);
  assert.match(js, /toLocaleString/);
  assert.match(js, /不可估算/);
  assert.doesNotMatch(js, /Number\(subtitles\.estimatedCostCny \|\| 0\)/);
  assert.match(js, /\/api\/model-settings/);
  assert.match(js, /\/api\/auth-cookies/);
  assert.match(js, /authToken = data\.authToken/);
  assert.match(js, /本地后台接口 \$\{path\} 返回 HTTP \$\{response\.status\}/);
  assert.doesNotMatch(js, /throw new Error\(payload\.error \|\| `HTTP/);
});

test("cookie import is platform-aware without exposing cookie values", () => {
  const html = fs.readFileSync(path.join(root, "web-player/index.html"), "utf8");
  const js = fs.readFileSync(path.join(root, "web-player/player.js"), "utf8");
  assert.match(html, /id="cookiePlatform"/);
  assert.match(html, /value="youtube"/);
  assert.match(html, /value="bilibili"/);
  assert.match(html, /value="twitch"/);
  assert.match(html, /id="chatTranslateToggle"/);
  assert.match(js, /platform:\s*el\("cookiePlatform"\)\.value/);
  assert.match(js, /SESSDATA/);
  assert.match(js, /data\.names/);
  assert.doesNotMatch(js, /data\.cookies|data\.values/);
});

test("session controls expose progress and preserve cleanup after media errors", () => {
  const html = fs.readFileSync(path.join(root, "web-player/index.html"), "utf8");
  const css = fs.readFileSync(path.join(root, "web-player/style.css"), "utf8");
  const js = fs.readFileSync(path.join(root, "web-player/player.js"), "utf8");
  assert.match(html, /id="mediaLoading"[^>]+role="status"[^>]+aria-live="polite"[^>]+hidden/);
  assert.match(css, /button\[aria-busy="true"\]::before/);
  assert.match(css, /\.loading-progress/);
  assert.match(js, /lastSessionState === "running" \|\| lastSessionState === "error"/);
  assert.match(js, /setAttribute\("aria-busy", "true"\)/);
  assert.match(js, /removeAttribute\("aria-busy"\)/);
  assert.doesNotMatch(js, /document\.addEventListener\("click"[^]*video\.play/);
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

test("the diagnostics bar is wired end to end and every id it needs exists", () => {
  const html = fs.readFileSync(path.join(root, "web-player/index.html"), "utf8");
  const css = fs.readFileSync(path.join(root, "web-player/style.css"), "utf8");
  const js = fs.readFileSync(path.join(root, "web-player/player.js"), "utf8");
  const module = fs.readFileSync(path.join(root, "web-player/diagnostics-log.js"), "utf8");
  const i18n = fs.readFileSync(path.join(root, "web-player/i18n.js"), "utf8");

  assert.match(html, /id="diagnosticsBar"/);
  assert.match(html, /id="diagToggle"/);
  assert.match(html, /id="diagSummary"/);
  assert.match(html, /id="diagList"/);
  assert.match(html, /id="diagCopy"/);
  assert.match(html, /id="diagClear"/);
  assert.match(html, /id="diagUpdate"/);
  assert.match(html, /id="diagBuild"/);
  assert.match(html, /diagnostics-log\.js/);
  // The collapsed bar must not cost vertical space: it lives inside the top
  // bar's own status area, and only the expanded panel leaves the flow.
  const topbar = html.slice(html.indexOf('<header class="topbar">'), html.indexOf("</header>"));
  assert.ok(topbar.includes('id="diagnosticsBar"'), "the bar belongs inside the top bar");
  assert.ok(!html.includes("topstack"), "the extra sticky wrapper is gone");
  assert.match(css, /\.diagbar\s*\{[^}]*flex:\s*1 1 0/);
  assert.match(css, /\.diagbar-body\s*\{[^}]*position:\s*absolute/);
  assert.match(css, /\.topbar\s*\{[^}]*position:\s*sticky/);
  assert.doesNotMatch(css, /\.topstack/, "the sticky wrapper's rules must be gone too");

  // The bar only reacts to errors, so player.js must not be able to swallow one
  // silently: the single showError funnel is what makes that guarantee cheap.
  assert.match(js, /function showError\(error\) \{[\s\S]{0,220}?diagnosticsBar\?\.push\("error", "ui"/);
  assert.match(js, /diagnosticsBar\?\.push\("error", "session"/);
  // push() is what expands the bar, so every call site must go through the bar.
  assert.doesNotMatch(js, /diagnosticsLog\?\.record\(/, "player.js must push through the bar, never the raw log");
  assert.match(js, /diagnosticsPoller = window\.createSerialPoller/);

  // Every element the module looks up must actually exist in the document.
  const wanted = [...module.matchAll(/getElementById\("([^"]+)"\)/g)].map((match) => match[1]);
  assert.ok(wanted.length >= 8, `expected the module to name its elements, saw ${wanted.length}`);
  for (const id of new Set(wanted)) {
    assert.match(html, new RegExp(`id="${id}"`), `index.html is missing #${id}`);
  }

  // Static chrome is bound for translation; the dynamic strings are in all five
  // locales. A key that resolves nowhere leaves Chinese text in an English UI.
  for (const key of ["diag.title", "diag.ok", "diag.copy", "diag.clear", "diag.empty"]) {
    assert.ok(i18n.includes(`"${key}"`), `i18n BINDINGS lost ${key}`);
  }
  for (const key of ["diag.hint.media", "diag.level.error", "diag.report.title", "diag.ctx.useragent"]) {
    assert.ok(i18n.includes(`"${key}"`), `i18n is missing ${key}`);
  }

  // The packaged build has no launcher script to preset an environment
  // variable, so these two buttons are the only way a user can produce a log
  // file at all. Losing the wiring silently removes that ability.
  assert.match(html, /id="diagLogToggle"/);
  assert.match(html, /id="diagLogOpen"/);
  assert.match(js, /el\("diagLogToggle"\)\?\.addEventListener\("click"/);
  assert.match(js, /el\("diagLogOpen"\)\?\.addEventListener\("click"/);
  // Starting a recording must also write out what already happened: the user is
  // only here because something already went wrong.
  assert.match(js, /function toggleDevLog[\s\S]{0,400}?devLogCursor = 0;/);
  for (const key of ["diag.log.record", "diag.log.stop", "diag.log.open", "diag.log.private"]) {
    const occurrences = i18n.split(`"${key}"`).length - 1;
    assert.equal(occurrences, 5, `${key} must exist in all five locales, found ${occurrences}`);
  }
  // Button labels are composed in JS, so they are outside the reverse-lookup
  // table i18n uses for dynamic sinks; the locale-change event is what keeps
  // them from freezing in the previous language.
  assert.match(i18n, /dispatchEvent\(new CustomEvent\("i18n:changed"/);
  assert.match(js, /addEventListener\("i18n:changed"/);
});

/*
 * The long-run soak measures whether subtitles reach the screen, and it can only
 * do that from outside the renderer. `__lingerlensSoakProbe` is the one seam it
 * reads, and `soak-onscreen-sampler.py` reads these exact field names -- so a
 * rename here breaks a two-hour measurement silently, at the end, with nothing
 * to show for it. Pin the contract rather than trusting the two to stay in step.
 */
test("the soak probe seam exists and matches what the sampler reads", () => {
  const js = fs.readFileSync(path.join(root, "web-player/player.js"), "utf8");
  const sampler = fs.readFileSync(path.join(root, "scripts/soak-onscreen-sampler.py"), "utf8");

  assert.match(sampler, /window\.__lingerlensSoakProbe/, "the sampler must call the probe");
  assert.match(js, /window\.__lingerlensSoakProbe = \(\) => \{/, "player.js must define it");
  // Read from the rendered DOM, not from scheduler state: the question is what is
  // on the glass, and internal state can disagree with it.
  assert.match(js, /__lingerlensSoakProbe[\s\S]{0,600}?el\("subtitleLayer"\)/);
  assert.match(js, /subtitle-content/);
  assert.match(js, /subtitle-src/);
  assert.match(js, /subtitle-zh/);
  assert.match(js, /dataset\.cueId/);
  // The media clock is the half that cannot be observed from outside; without it
  // the samples record what was on screen but not *when* on the media timeline.
  assert.match(js, /__lingerlensSoakProbe[\s\S]{0,600}?wall: playingWallClock\(\)/);

  for (const field of ["at", "wall", "currentTime", "paused", "playbackRate",
    "readyState", "sessionState", "hidden", "rows"]) {
    assert.ok(new RegExp(`\\b${field}:`).test(js), `the probe must expose ${field}`);
  }
});

test("realtime workbench UI elements and live message contracts are wired", () => {
  const html = fs.readFileSync(path.join(root, "web-player/index.html"), "utf8");
  const css = fs.readFileSync(path.join(root, "web-player/style.css"), "utf8");
  const js = fs.readFileSync(path.join(root, "web-player/player.js"), "utf8");

  // Workbench layout and tabs
  assert.match(html, /id="workbenchContainer"/);
  assert.match(html, /id="tabWorkbenchSplit"/);
  assert.match(html, /id="tabWorkbenchSubtitles"/);
  assert.match(html, /id="tabWorkbenchChat"/);
  assert.match(html, /id="paneSubtitles"/);
  assert.match(html, /id="paneChat"/);
  assert.match(html, /id="subtitlesTimelineList"/);
  assert.match(html, /id="chatTimelineList"/);
  assert.match(html, /id="chatTranslateToggle"/);
  assert.match(html, /id="chatStatsIndicator"/);
  assert.match(html, /id="followSubtitlesBtn"/);
  assert.match(html, /id="followChatBtn"/);
  assert.match(html, /id="clearSubtitlesTimelineBtn"/);
  assert.match(html, /id="clearChatTimelineBtn"/);

  // Script tags in html
  assert.match(html, /media-clock\.js/);
  assert.match(html, /playback-recovery\.js/);
  assert.match(html, /poll-loop\.js/);
  assert.match(html, /workbench-controller\.js/);
  assert.match(html, /pane-resizer\.js/);
  assert.match(html, /live-messages-client\.js/);

  // Responsive and CSS layout. The layout is a 1:1 port of the authoritative
  // design prototype (prototype/redesign/style.css), so these pin the
  // prototype's numbers: fixed 320px side panes, a full-bleed section 01, and
  // the prototype's own breakpoints plus one small-screen fallback it lacks.
  assert.match(css, /\.workbench\s*\{/);
  // Side panes are still the prototype's fixed 320px, now expressed as variables
  // so they can be dragged. The defaults are what keep the port 1:1, so both the
  // default values and the tracks that consume them are pinned.
  assert.match(css, /--pane-left-w:\s*320px/);
  assert.match(css, /--pane-right-w:\s*320px/);
  assert.match(css, /grid-template-columns:\s*var\(--pane-left-w\)\s+minmax\(0,\s*1fr\)\s+var\(--pane-right-w\)/);
  assert.match(css, /\.workbench\.view-subtitles\s*\{[^}]*grid-template-columns:\s*var\(--pane-left-w\)\s+minmax\(0,\s*1fr\)/);
  assert.match(css, /\.workbench\.view-chat\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+var\(--pane-right-w\)/);
  // Regression guard: the <=1240 block used to give every view the same 280px
  // first track, which squeezed the video into 280px in chat view. Chat view
  // must keep the stage first and the sidebar second.
  assert.match(css, /@media \(max-width:\s*1240px\)[\s\S]*?\.workbench\.view-chat\s*\{\s*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+var\(--pane-right-w\)/);
  assert.match(css, /@media \(max-width:\s*1240px\)[\s\S]*?--pane-left-w:\s*280px/);
  assert.match(css, /\.deck-wide[^}]+width:\s*calc\(100vw - 32px\)/s);
  assert.match(css, /@media \(max-width:\s*1240px\)/);
  assert.match(css, /@media \(max-width:\s*899px\)/);
  assert.match(css, /\.pane\s*\{/);
  assert.match(css, /\.timeline\s*\{/);
  assert.match(css, /\.timeline-row/);
  assert.match(css, /\.active-cue/);
  assert.match(css, /\.player-controls/);
  assert.match(css, /\.player-stage\.controls-visible \.player-controls/);
  assert.match(css, /\.stage-fullscreen/);
  // Resizable sidebars: one handle per pane, correct splitter semantics, and the
  // handles must be transparent at rest so the ported layout is untouched.
  assert.equal((html.match(/class="pane-resizer"/g) || []).length, 2);
  assert.match(html, /data-pane-edge="subtitles"/);
  assert.match(html, /data-pane-edge="chat"/);
  assert.match(html, /role="separator"/);
  assert.match(html, /aria-orientation="vertical"/);
  assert.match(html, /aria-controls="paneSubtitles"/);
  assert.match(html, /aria-controls="paneChat"/);
  assert.match(css, /\.pane-resizer\s*\{[^}]*background:\s*transparent/);
  assert.match(css, /\.pane-resizer\s*\{[^}]*cursor:\s*col-resize/);
  assert.match(css, /@media \(max-width:\s*899px\)[\s\S]*?\.pane-resizer\s*\{\s*display:\s*none/);
  const paneResizer = fs.readFileSync(path.join(root, "web-player/pane-resizer.js"), "utf8");
  assert.match(paneResizer, /lingerlens\.paneWidths/);
  assert.match(paneResizer, /--pane-left-w/);
  assert.match(paneResizer, /--pane-right-w/);
  // At rest nothing may be written: the inline variable is the whole reason the
  // default layout still matches the prototype.
  assert.match(js, /window\.createPaneResizer/);
  // The fullscreen control lives in the right-hand cluster of the control bar,
  // exactly as in the prototype; it must stay inside .ctl-cluster and keep the
  // player-control-surface hook the icon rules are keyed on.
  assert.match(html, /id="toggleFullscreen" class="[^"]*player-control-surface[^"]*"/);
  assert.match(html, /<div class="ctl-cluster">[\s\S]*id="toggleFullscreen"[\s\S]*?<\/div>\s*<\/div>\s*<\/div>\s*<\/section>/);
  assert.match(html, /class="icon-play"/);
  assert.match(html, /class="icon-muted"/);
  assert.doesNotMatch(html, /id="returnLive"/);
  assert.doesNotMatch(html, />播放<|>静音<|>全屏<|回到直播/);
  assert.match(js, /setTimeout\(\(\) => setPlayerControlsVisible\(false\), 1600\)/);
  assert.match(js, /stage\.addEventListener\("pointermove", schedulePlayerControlsHide\)/);
  assert.doesNotMatch(js, /el\("returnLive"\)/);
  assert.match(js, /const cutoff = newest - 605/);
  assert.match(js, /const maxEntries = 100/);
  assert.match(js, /createSerialPoller/);
  assert.match(js, /hiddenIntervalMs:\s*5000/);
  assert.match(js, /hiddenIntervalMs:\s*2000/);
  assert.match(js, /visibilitychange/);
  assert.doesNotMatch(js, /setInterval\(refreshStatus/);
  assert.doesNotMatch(js, /setInterval\(refreshSubtitles/);

  // The design-token layer is the prototype's, verbatim. These catch a partial
  // or accidental replacement of the neo-brutalist palette / structural rules.
  assert.match(css, /--paper:\s*#F4F0E6/);
  assert.match(css, /--card:\s*#FFFDF6/);
  assert.match(css, /--camellia:\s*#c41d42/);
  assert.match(css, /--rose:\s*#0f9cb8/);
  assert.match(css, /--font-display:\s*"Archivo Black"/);
  assert.match(css, /--font-ui:\s*"Noto Sans SC"/);
  assert.match(css, /--font-mono:\s*"JetBrains Mono"/);
  // Fonts are vendored same-origin (no CDN); fonts.css is linked, and the
  // generated sheet must actually reference the local files.
  assert.match(html, /<link rel="stylesheet" href="\/fonts\.css">/);
  const fontsCssPath = path.join(root, "web-player/fonts.css");
  assert.ok(fs.existsSync(fontsCssPath), "web-player/fonts.css must exist (scripts/fetch-fonts.py)");
  const fontsCss = fs.readFileSync(fontsCssPath, "utf8");
  assert.match(fontsCss, /font-family:"Archivo Black"/);
  assert.match(fontsCss, /font-family:"JetBrains Mono"/);
  assert.match(fontsCss, /font-family:"Noto Sans SC"/);
  assert.match(fontsCss, /url\(\/fonts\/noto-sans-sc\//);
  assert.doesNotMatch(fontsCss, /https?:\/\//);
  assert.match(css, /\.provider-profile\s*\{/);
  assert.match(css, /\.provider-fields\s*\{/);
  assert.match(css, /\.lang-combo-list\s*\{/);
  assert.match(css, /\.subtitle-layer\s*\{/);
  // The three settings groups keep their per-group class hooks inside the
  // prototype's .sgroup box; i18n resolves the target-language label through
  // `.language-controls .language-field:last-child`, so that one is load-bearing.
  assert.match(html, /class="sgroup subtitle-controls"/);
  assert.match(html, /class="sgroup chat-display-settings"/);
  assert.match(html, /class="sgroup language-controls"/);
  assert.match(css, /\.settings-dialog\s*\{/);
  assert.match(css, /\.settings-footer\s*\{/);
  // Section 03 is the prototype's three-column settings group grid, and
  // section 04 its four-column telemetry card grid — not collapsible <details>.
  assert.match(css, /\.settings-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/s);
  assert.match(css, /\.sgroup\s*\{/);
  assert.match(css, /\.sgroup-tag\s*\{/);
  assert.match(css, /\.telemetry-grid\s*\{[^}]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\)/s);
  assert.match(css, /\.tcard\s*\{/);
  assert.match(css, /\.tcard-indigo\s*\{/);
  assert.match(css, /\.tcard-lime\s*\{/);
  assert.match(css, /\.tcard-yellow\s*\{/);
  assert.doesNotMatch(html, /<details class="preference-group"/);
  assert.doesNotMatch(html, /class="runtime-details"/);
  // Shell chrome ported from the prototype.
  assert.match(html, /class="state-badge"/);
  assert.match(html, /class="state-dot"/);
  assert.match(html, /id="topClock"/);
  assert.match(html, /id="liveChip"/);
  assert.match(css, /\.topbar-clock\s*\{/);
  assert.match(css, /\.live-chip\s*\{/);
  assert.match(css, /\.reveal\s*\{/);
  assert.ok(/class="deck[^"]*\breveal\b/.test(html), "sections must carry the .reveal class");
  // A modal over the live video must not trigger a full-page backdrop blur;
  // Chromium/Electron otherwise recomposites the video and animated chat on
  // every frame while the model settings dialog is open.
  assert.doesNotMatch(css, /\.settings-dialog::backdrop[^}]*backdrop-filter\s*:/);
  assert.doesNotMatch(css, /var\(--(?:surface|text-dim|text-muted|accent|radius-sm|radius-md)\)/);

  // player.js wiring
  assert.match(js, /createMediaClock/);
  assert.match(js, /createWorkbenchController/);
  assert.match(js, /createLiveMessagesClient/);
  assert.match(js, /createLiveMessagesTimeline/);
  assert.match(js, /createFollowModeController/);
  assert.match(js, /renderSubtitlesTimeline/);
});
