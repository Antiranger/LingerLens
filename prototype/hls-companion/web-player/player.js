(() => {
  const el = (id) => document.getElementById(id);
  const video = el("video");
  const stage = document.querySelector(".player-stage");
  const state = document.querySelector(".state");
  let hls = null;
  let lastPlaylistUrl = null;
  let browserLatency = null;
  const initialParams = new URLSearchParams(location.search);
  let authToken = initialParams.get("authToken");
  let statusTimer = null;
  let subtitleTimer = null;
  let subtitleRenderTimer = null;
  let latestLevelDetails = null;
  let subtitleCues = new Map();
  let subtitleAfterSeq = 0;
  let subtitleMaxKnownEnd = 0;
  let providerCatalog = null;
  const subtitleScheduler = window.createSubtitleScheduler
    ? window.createSubtitleScheduler({ minDwell: 1.2, maxLateSeconds: 2.0, bridgeGap: 0.3 })
    : null;
  const subtitleWindow = window.createSubtitleWindowController
    ? window.createSubtitleWindowController({ stage, windowElement: el("subtitleLayer"), storage: localStorage, fullscreenDocument: document })
    : null;
  const subtitleBudget = { lowSince: null, suggested: null };
  const savedTargetDelay = Number(localStorage.getItem("laglingo.targetDelaySeconds") || 15);
  const subtitlePrefs = {
    enabled: localStorage.getItem("laglingo.subtitle.enabled") !== "false",
    mode: localStorage.getItem("laglingo.subtitle.mode") || "bilingual",
    size: localStorage.getItem("laglingo.subtitle.size") || "medium",
    offset: Number(localStorage.getItem("laglingo.subtitle.offset") || 0),
  };
  if (initialParams.get("url")) el("url").value = initialParams.get("url");
  el("targetDelay").value = String(savedTargetDelay > 10 && savedTargetDelay <= 60 ? savedTargetDelay : 15);

  const setState = (text, tone = "idle") => {
    el("stateText").textContent = text;
    state.dataset.tone = tone;
  };

  const seconds = (value) => Number.isFinite(value) ? `${value.toFixed(1)} 秒` : "—";
  const integer = (value) => Number(value || 0).toLocaleString("zh-CN");
  const cny = (value, reason) => Number.isFinite(Number(value))
    ? `¥${Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 4, maximumFractionDigits: 6 })}`
    : `不可估算${reason ? `（${reason}）` : ""}`;

  async function request(path, body) {
    const response = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
    });
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch { payload = { error: text }; }
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  function commonBody() {
    const body = { url: el("url").value.trim() };
    if (authToken) body.authToken = authToken;
    return body;
  }

  async function probe() {
    setBusy(true);
    setState("正在探测", "waiting");
    el("message").textContent = "当前版 yt-dlp 正在读取可用格式。登录受限直播需要扩展授权，或用 --cookies-from-browser 启动 Companion。";
    try {
      const data = await request("/api/probe", commonBody());
      const select = el("quality");
      select.innerHTML = '<option value="auto">自动（最高兼容）</option>';
      for (const quality of data.qualities) {
        const option = document.createElement("option");
        option.value = quality.qualityId;
        option.textContent = `${quality.label}${quality.estimatedBitrate ? ` · ${(quality.estimatedBitrate / 1e6).toFixed(1)} Mbps` : ""}`;
        option.disabled = quality.requiresTranscode;
        select.append(option);
      }
      const preferred = data.qualities.find((quality) => quality.height === 1080 && !quality.requiresTranscode)
        || data.qualities.find((quality) => quality.height === 720 && !quality.requiresTranscode);
      select.value = preferred?.qualityId || "auto";
      select.disabled = false;
      el("start").disabled = false;
      el("streamTitle").textContent = data.title || "直播格式已就绪";
      el("message").textContent = `发现 ${data.qualities.length} 个候选输出。灰色选项需要转码，本原型不会自动使用。`;
      setState("格式已就绪", "active");
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  async function start() {
    setBusy(true);
    setState("启动合流", "waiting");
    destroyPlayer();
    try {
      const body = {
        ...commonBody(),
        qualityId: el("quality").value,
        targetDelaySeconds: Number(el("targetDelay").value),
        subtitles: {
          enabled: el("subtitlesEnabled").checked,
          sourceLanguage: "ja",
          targetLanguage: el("targetLanguage").value,
        },
      };
      const data = await request("/api/start", body);
      localStorage.setItem("laglingo.targetDelaySeconds", String(body.targetDelaySeconds));
      el("stop").disabled = false;
      const quality = data.quality;
      el("resolution").textContent = `${quality.width || "?"}×${quality.height || "?"}${quality.fps ? ` @ ${quality.fps}fps` : ""}`;
      el("message").textContent = "yt-dlp 正在下载直播，FFmpeg 只负责本地 CMAF 封装；公开播放列表会在延迟预算满足后出现。";
      setState("建立延迟缓冲", "waiting");
      authToken = null;
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  function resetStoppedUi() {
    destroyPlayer();
    stage.classList.remove("has-media");
    el("quality").disabled = true;
    el("quality").innerHTML = '<option value="auto">自动（最高兼容）</option>';
    el("start").disabled = true;
    el("stop").disabled = true;
    el("streamTitle").textContent = "等待直播地址";
    el("message").textContent = "已停止当前直播。可以输入另一个 YouTube 或 Bilibili 地址继续探测。";
    for (const id of ["hiddenDelay", "playerDelay", "totalDelay", "buffer", "resolution", "uptime", "subtitleProviderStatus", "asrUsageCost", "translationUsageCost", "totalUsageCost", "translationLatency", "subtitleReadyLag", "budgetMargin", "cueDuration", "timingSources", "schedulerDrops"]) {
      el(id).textContent = "—";
    }
    setState("已停止", "idle");
  }

  async function stop() {
    setBusy(true);
    try {
      await request("/api/stop", {});
      resetStoppedUi();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  function attach(url) {
    if (lastPlaylistUrl === url && hls) return;
    destroyPlayer();
    lastPlaylistUrl = url;
    if (window.Hls?.isSupported()) {
      hls = new Hls({
        lowLatencyMode: false,
        // 分片时长固定为 1 秒（split_by_time），所以这些「个数」等于秒数。
        //
        // 播放头到公开直播边缘的距离 *就是* 浏览器能攒下的前向缓冲：直播流里
        // 边缘之后没有媒体可下。坐在边缘后 3 秒 = 只有 3 秒缓冲，任何一次分片
        // 拉取变慢或解码卡顿都会立刻耗尽它，表现为进度条反复卡住。
        // 加大服务端的 publish delay 救不了这一点 —— 公开边缘会同步前移，
        // 播放头依然只落后边缘 3 秒。缓冲必须在播放器这一侧要。
        liveSyncDurationCount: 12,
        // 必须显著大于 liveSyncDurationCount，否则 hls.js 会不停判定「延迟超标」
        // 并强制前跳，那本身就是卡顿感的来源。
        liveMaxLatencyDurationCount: 40,
        maxBufferLength: 90,
        backBufferLength: 30,
        enableWorker: true,
      });
      hls.loadSource(`${url}?t=${Date.now()}`);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => attemptAutoplay());
      hls.on(Hls.Events.LEVEL_UPDATED, (_, data) => {
        latestLevelDetails = data?.details || null;
        const edge = data?.details?.edge;
        browserLatency = Number.isFinite(edge) && Number.isFinite(video.currentTime) ? Math.max(0, edge - video.currentTime) : null;
      });
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (!data.fatal) return;
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR) hls.startLoad();
        else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
        else showError(new Error(`hls.js fatal: ${data.details}`));
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = url;
      attemptAutoplay();
    } else {
      showError(new Error("此浏览器不支持 MSE/HLS"));
    }
    stage.classList.add("has-media");
  }

  function attemptAutoplay() {
    video.play().then(() => {
      if (video.muted) {
        el("message").textContent = "已静音开播（浏览器自动播放限制）。在播放器上取消静音即可听到声音。";
      }
    }).catch(() => {
      setState("自动播放被拦截", "waiting");
      el("message").textContent = "浏览器拦截了自动播放。点击播放器的播放按钮即可开始。";
    });
  }

  document.addEventListener("click", () => {
    if (video.paused && (hls || video.src)) video.play().catch(() => {});
  });

  function destroyPlayer() {
    if (hls) hls.destroy();
    hls = null;
    lastPlaylistUrl = null;
    browserLatency = null;
    latestLevelDetails = null;
    subtitleCues.clear();
    subtitleAfterSeq = 0;
    subtitleMaxKnownEnd = 0;
    if (subtitleScheduler) subtitleScheduler.reset();
    clearSubtitle();
    video.removeAttribute("src");
    video.load();
  }

  async function refreshStatus() {
    try {
      const data = await request("/api/status");
      // The stream may have been started from another tab, the extension, or a
      // diagnostic client. Restore controls from server truth on every poll
      // instead of relying on this page's start() call having run.
      const serverActive = data.state === "running";
      el("stop").disabled = !serverActive;
      if (data.playlistUrl) attach(data.playlistUrl);
      if (data.state === "error") throw new Error(data.error || "FFmpeg failed");
      if (data.state === "running") {
        const playing = data.playlistReady && !video.paused;
        setState(data.playlistReady ? (playing ? "延迟播放中" : "就绪待播放") : "建立延迟缓冲", playing ? "stable" : "waiting");
      }
      if (data.state === "idle" && (hls || video.src)) resetStoppedUi();
      el("hiddenDelay").textContent = seconds(Number(data.hiddenMediaSeconds));
      const playerBehind = Number.isFinite(browserLatency) ? browserLatency : estimateVideoLatency();
      el("playerDelay").textContent = seconds(playerBehind);
      const measuredDelay = Number(data.sourceDelaySeconds || 0) + Number(data.hiddenMediaSeconds || 0) + (playerBehind || 0);
      el("totalDelay").textContent = seconds(measuredDelay);
      if (data.state === "running" && Number(data.targetDelaySeconds) > 10) {
        el("targetDelay").value = String(data.targetDelaySeconds);
        localStorage.setItem("laglingo.targetDelaySeconds", String(data.targetDelaySeconds));
      }
      el("buffer").textContent = seconds(bufferAhead());
      el("uptime").textContent = seconds(Number(data.uptimeSeconds));
      const subtitles = data.subtitles || {};
      el("subtitleProviderStatus").textContent = subtitles.asrProviderId ? `${subtitles.asrProviderId} / ${subtitles.translationProviderId || "原文"}` : "未运行";
      const asrUsage = subtitles.asrUsage || { seconds: subtitles.asrSeconds || 0 };
      const translationUsage = subtitles.translationUsage || {};
      el("asrUsageCost").textContent = `${Number(asrUsage.seconds || 0).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} 秒 · ${cny(subtitles.asrEstimatedCostCny, subtitles.asrEstimateReason)}`;
      el("translationUsageCost").textContent = `输入 ${integer(translationUsage.nonCachedInputTokens)} · 缓存 ${integer(translationUsage.cachedInputTokens)} · 输出 ${integer(translationUsage.outputTokens)} · ${cny(subtitles.translationEstimatedCostCny, subtitles.translationEstimateReason)}`;
      el("totalUsageCost").textContent = cny(subtitles.totalEstimatedCostCny, subtitles.totalEstimateReason);
      const latency = subtitles.avgTranslationLatencyMs;
      el("translationLatency").textContent = Number.isFinite(Number(latency)) ? `${(Number(latency) / 1000).toFixed(2)} 秒` : "—";
      updateSubtitleBudget(subtitles, measuredDelay, Number(data.targetDelaySeconds));
      if (data.quality) el("resolution").textContent = `${data.quality.width || "?"}×${data.quality.height || "?"}${data.quality.fps ? ` @ ${data.quality.fps}fps` : ""}`;
    } catch (error) {
      if (!String(error.message).includes("Failed to fetch")) showError(error);
    }
  }

  function estimateVideoLatency() {
    if (!video.seekable.length) return null;
    return Math.max(0, video.seekable.end(video.seekable.length - 1) - video.currentTime);
  }

  function bufferAhead() {
    for (let i = 0; i < video.buffered.length; i += 1) {
      if (video.currentTime >= video.buffered.start(i) && video.currentTime <= video.buffered.end(i)) {
        return Math.max(0, video.buffered.end(i) - video.currentTime);
      }
    }
    return 0;
  }

  async function openModelSettings() {
    const dialog = el("modelSettingsDialog");
    const feedback = el("modelSettingsFeedback");
    feedback.textContent = "正在读取本机配置…";
    feedback.dataset.tone = "";
    dialog.showModal();
    try {
      providerCatalog = await request("/api/model-settings");
      renderProviderProfiles();
      feedback.textContent = "";
    } catch (error) {
      feedback.textContent = error.message || String(error);
      feedback.dataset.tone = "error";
    }
  }

  const providerKinds = {
    asr: [
      ["dashscope-qwen-realtime", "DashScope Qwen Realtime"],
      ["dashscope-task-asr", "DashScope Task ASR"],
      ["openai-audio-transcriptions", "OpenAI Audio Transcriptions"],
    ],
    translation: [
      ["openai-compatible", "OpenAI Compatible"],
      ["qwen-mt", "Qwen MT"],
    ],
  };

  function newProviderId(section) {
    const prefix = section === "asr" ? "asr" : "translation";
    const used = new Set(providerCatalog[section].providers.map((item) => item.id));
    let index = 1;
    while (used.has(`${prefix}-${index}`)) index += 1;
    return `${prefix}-${index}`;
  }

  function addProvider(section) {
    const id = newProviderId(section);
    const asr = section === "asr";
    providerCatalog[section].providers.push({
      id,
      label: asr ? "新 ASR Provider" : "新翻译 Provider",
      kind: asr ? "openai-audio-transcriptions" : "openai-compatible",
      model: asr ? "whisper-1" : "",
      baseUrl: asr ? "http://127.0.0.1:8000/v1" : "http://127.0.0.1:8000/v1",
      apiKey: "",
      options: asr
        ? { language: "ja", windowSeconds: 3, requestTimeoutSeconds: 20 }
        : { temperature: 0.3, maxTokens: 256, timeoutSeconds: 6, contextPairs: 6 },
    });
    renderProviderProfiles();
  }

  function renderProviderProfiles() {
    if (!providerCatalog) return;
    renderProviderSection("asr", el("asrProfiles"));
    renderProviderSection("translation", el("translationProfiles"));
  }

  function renderProviderSection(section, container) {
    const group = providerCatalog[section];
    container.innerHTML = "";
    group.providers.forEach((provider) => {
      const card = document.createElement("article");
      card.className = "provider-profile";
      card.dataset.providerId = provider.id;
      const isActive = group.active === provider.id;
      const cannotDelete = isActive || group.providers.length === 1;
      const kinds = providerKinds[section].map(([value, label]) =>
        `<option value="${value}"${provider.kind === value ? " selected" : ""}>${label}</option>`
      ).join("");
      card.innerHTML = `
        <header class="provider-profile-head">
          <label class="active-provider"><input type="radio" name="active-${section}" data-action="active" ${isActive ? "checked" : ""}> 当前使用</label>
          <code>${escapeHtml(provider.id)}</code>
          <button class="secondary compact provider-delete" type="button" data-action="delete" ${cannotDelete ? "disabled" : ""}>删除</button>
        </header>
        <div class="provider-fields">
          <label><span>名称</span><input data-field="label" value="${escapeHtml(provider.label || "")}" required></label>
          <label><span>协议</span><select data-field="kind">${kinds}</select></label>
          <label><span>模型</span><input data-field="model" value="${escapeHtml(provider.model || "")}" required></label>
          <label><span>Base URL</span><input data-field="baseUrl" value="${escapeHtml(provider.baseUrl || "")}" required></label>
          <label class="wide"><span>API Key（原文显示）</span><input data-field="apiKey" type="text" value="${escapeHtml(provider.apiKey || "")}" autocomplete="off"><small>本窗口会直接显示保存的 Key；共享屏幕、截图或旁观者都可能看到。</small></label>
          ${section === "asr" ? asrPricingFields(provider) : translationPricingFields(provider)}
          ${section === "asr" ? asrOptionFields(provider) : translationOptionFields(provider)}
        </div>`;
      card.addEventListener("input", handleProviderInput);
      card.addEventListener("change", handleProviderInput);
      card.querySelector('[data-action="active"]').addEventListener("change", () => {
        group.active = provider.id;
        renderProviderProfiles();
      });
      card.querySelector('[data-action="delete"]').addEventListener("click", () => {
        if (group.active === provider.id || group.providers.length <= 1) return;
        group.providers = group.providers.filter((item) => item.id !== provider.id);
        if (section === "translation") group.fallback = (group.fallback || []).filter((id) => id !== provider.id);
        renderProviderProfiles();
      });
      container.append(card);
    });
  }

  function pricingValue(value) {
    return value === null || value === undefined ? "" : escapeHtml(value);
  }

  function asrPricingFields(provider) {
    return `<label class="wide"><span>ASR 单价（CNY / 秒）</span><input data-field="pricePerSecondCny" type="number" min="0" step="any" value="${pricingValue(provider.pricePerSecondCny)}" placeholder="留空表示不可估算"><small>本地免费服务请显式填写 0；留空不是免费。</small></label>`;
  }

  function translationPricingFields(provider) {
    return `
      <label><span>普通输入（CNY / 百万 token）</span><input data-field="pricePerMillionInputTokensCny" type="number" min="0" step="any" value="${pricingValue(provider.pricePerMillionInputTokensCny)}" placeholder="留空不可估算"></label>
      <label><span>缓存输入（CNY / 百万 token）</span><input data-field="pricePerMillionCachedInputTokensCny" type="number" min="0" step="any" value="${pricingValue(provider.pricePerMillionCachedInputTokensCny)}" placeholder="留空不可估算"></label>
      <label><span>输出（CNY / 百万 token）</span><input data-field="pricePerMillionOutputTokensCny" type="number" min="0" step="any" value="${pricingValue(provider.pricePerMillionOutputTokensCny)}" placeholder="留空不可估算"></label>`;
  }

  function asrOptionFields(provider) {
    const options = provider.options || {};
    if (provider.kind !== "openai-audio-transcriptions") return "";
    return `
      <label><span>语言</span><input data-option="language" value="${escapeHtml(options.language || "ja")}"></label>
      <label><span>分窗秒数</span><input data-option="windowSeconds" type="number" min="0.5" step="0.5" value="${Number(options.windowSeconds || 3)}"></label>
      <label><span>请求超时（秒）</span><input data-option="requestTimeoutSeconds" type="number" min="1" step="1" value="${Number(options.requestTimeoutSeconds || 20)}"></label>`;
  }

  function translationOptionFields(provider) {
    const options = provider.options || {};
    return `
      <label><span>温度</span><input data-option="temperature" type="number" min="0" max="2" step="0.1" value="${Number(options.temperature ?? 0.3)}"></label>
      <label><span>最大 Tokens</span><input data-option="maxTokens" type="number" min="32" step="1" value="${Number(options.maxTokens || 256)}"></label>
      <label><span>超时（秒）</span><input data-option="timeoutSeconds" type="number" min="1" step="1" value="${Number(options.timeoutSeconds || 6)}"></label>
      <label><span>上下文对数</span><input data-option="contextPairs" type="number" min="0" step="1" value="${Number(options.contextPairs ?? 6)}"></label>`;
  }

  function handleProviderInput(event) {
    const card = event.currentTarget;
    const section = card.closest("#asrProfiles") ? "asr" : "translation";
    const provider = providerCatalog[section].providers.find((item) => item.id === card.dataset.providerId);
    if (!provider) return;
    const field = event.target.dataset.field;
    const option = event.target.dataset.option;
    if (field) {
      provider[field] = event.target.type === "number"
        ? (event.target.value === "" ? null : Number(event.target.value))
        : event.target.value;
    }
    if (option) {
      provider.options ||= {};
      provider.options[option] = event.target.type === "number" ? Number(event.target.value) : event.target.value;
    }
    if (field === "kind") renderProviderProfiles();
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  }

  function closeModelSettings() {
    el("modelSettingsDialog").close();
  }

  function updateCookiePlatformHelp() {
    const bilibili = el("cookiePlatform").value === "bilibili";
    el("cookieImportIntro").textContent = bilibili
      ? "从 bilibili.com 的 DevTools Cookie 列表复制名称/值，或粘贴 Cookie 请求头 / Netscape 文件。yt-dlp 登录检查只要求 SESSDATA；其他有效 Cookie 会一并保留。"
      : "从 youtube.com（必要时包括 Google 登录域）的 DevTools Cookie 列表复制名称/值，或粘贴 Cookie 请求头 / Netscape 文件。";
    el("cookiePayload").placeholder = bilibili
      ? "SESSDATA　xxxxxxxx…\nbili_jct　yyyyyyyy…\nDedeUserID　12345"
      : "SID　xxxxxxxx…\nHSID　yyyyyyyy…\n（直接从 DevTools Cookie 列表复制即可）";
  }

  function openCookieImport() {
    const feedback = el("cookieImportFeedback");
    feedback.textContent = "";
    feedback.dataset.tone = "";
    updateCookiePlatformHelp();
    el("cookieImportDialog").showModal();
  }

  function closeCookieImport() {
    el("cookieImportDialog").close();
  }

  async function submitCookieImport(event) {
    event.preventDefault();
    const feedback = el("cookieImportFeedback");
    const submit = el("submitCookieImport");
    feedback.textContent = "正在导入…";
    feedback.dataset.tone = "";
    submit.disabled = true;
    try {
      const payload = { platform: el("cookiePlatform").value };
      if (el("cookieFormat").value === "netscape") {
        payload.netscape = el("cookiePayload").value;
      } else if (el("cookieFormat").value === "lines") {
        payload.lines = el("cookiePayload").value;
      } else {
        payload.header = el("cookiePayload").value;
      }
      const data = await request("/api/auth-cookies", payload);
      authToken = data.authToken;
      if (data.missingCritical && data.missingCritical.length) {
        feedback.textContent = data.platform === "bilibili"
          ? `已保存 ${data.accepted} 个 Bilibili Cookie，但缺少 yt-dlp 登录关键字段 SESSDATA。请从 bilibili.com 的 Cookie 列表重新复制。`
          : `已保存 ${data.accepted} 个 YouTube Cookie，但缺少登录关键字段：${data.missingCritical.join("、")}。请从 YouTube/Google 登录域的 Cookie 列表重新复制。`;
        feedback.dataset.tone = "error";
      } else {
        const platformName = data.platform === "bilibili" ? "Bilibili" : "YouTube";
        feedback.textContent = `已导入 ${data.accepted} 个 ${platformName} Cookie（${data.names.join("、")}）${data.persisted ? "，已按平台保存到本机" : "，磁盘保存失败，仅本次运行有效"}。现在可以探测对应平台地址。`;
        feedback.dataset.tone = "success";
      }
      el("message").textContent = "登录 Cookie 已导入，探测与启动将使用该登录态。";
    } catch (error) {
      feedback.textContent = error.message || String(error);
      feedback.dataset.tone = "error";
    } finally {
      submit.disabled = false;
    }
  }

  async function saveModelSettings(event) {
    event.preventDefault();
    const feedback = el("modelSettingsFeedback");
    const save = el("saveModelSettings");
    save.disabled = true;
    feedback.textContent = "正在保存…";
    feedback.dataset.tone = "";
    try {
      const payload = structuredClone(providerCatalog);
      const data = await request("/api/model-settings", payload);
      providerCatalog = data;
      renderProviderProfiles();
      feedback.textContent = "已保存。下次启动直播时使用新配置。";
      feedback.dataset.tone = "success";
      el("targetLanguage").value = data.subtitle?.targetLanguage || el("targetLanguage").value;
    } catch (error) {
      feedback.textContent = error.message || String(error);
      feedback.dataset.tone = "error";
    } finally {
      save.disabled = false;
    }
  }

  async function loadSubtitleDefaults() {
    try {
      const data = await request("/api/providers");
      el("targetLanguage").value = data.subtitle?.targetLanguage || "zh";
    } catch (error) {
      showError(error);
    }
  }

  // 延迟预算闭环（redesign Fix E）：预算余量 = 当前总观看延迟 − p95(readyLag)。
  // 余量 < 0.5s 持续 30 秒就给出可执行建议：一键抬高发布延迟。
  function cueDurationPercentiles() {
    const spans = [];
    for (const cue of subtitleCues.values()) {
      if (Number.isFinite(cue.tStart) && Number.isFinite(cue.tEnd) && cue.tEnd > cue.tStart) {
        spans.push(cue.tEnd - cue.tStart);
      }
    }
    if (!spans.length) return null;
    spans.sort((a, b) => a - b);
    const at = (f) => spans[Math.min(spans.length - 1, Math.max(0, Math.round(f * (spans.length - 1))))];
    return { p50: at(0.5), p95: at(0.95) };
  }

  function updateSubtitleBudget(subtitles, totalDelaySeconds, targetDelaySeconds) {
    const p50 = Number(subtitles.readyLagP50);
    const p95 = Number(subtitles.readyLagP95);
    const hasStats = Number.isFinite(p95) && Number.isFinite(Number(totalDelaySeconds));

    const counts = subtitles.timingSourceCounts || {};
    const total = Object.values(counts).reduce((sum, n) => sum + Number(n || 0), 0);
    el("timingSources").textContent = total
      ? `asr ${Math.round((100 * Number(counts.asr || 0)) / total)}% · approx ${Number(counts.approx || 0)}`
      : "—";
    const scheduler = subtitleScheduler ? subtitleScheduler.stats : null;
    el("schedulerDrops").textContent = scheduler
      ? `${scheduler.droppedLateCues} 丢 / ${scheduler.lateCues} 迟到`
      : "—";

    const spans = cueDurationPercentiles();
    el("cueDuration").textContent = spans ? `${spans.p50.toFixed(1)}s / ${spans.p95.toFixed(1)}s` : "—";

    el("subtitleReadyLag").textContent = hasStats
      ? `${Number.isFinite(p50) ? p50.toFixed(2) : "—"}s / ${p95.toFixed(2)}s`
      : "—";
    if (!hasStats) {
      el("budgetMargin").textContent = "—";
      subtitleBudget.lowSince = null;
      subtitleBudget.suggested = null;
      el("applyDelayButton").hidden = true;
      return;
    }
    // Cues are displayed from tStart, so the delay must cover the sentence
    // itself as well as the time spent producing its translation.
    const margin = totalDelaySeconds - p95 - (spans ? spans.p95 : 0);
    el("budgetMargin").textContent = seconds(margin);
    if (margin >= 0.5) {
      subtitleBudget.lowSince = null;
      subtitleBudget.suggested = null;
      el("applyDelayButton").hidden = true;
      return;
    }
    if (!subtitleBudget.lowSince) subtitleBudget.lowSince = Date.now();
    if (Date.now() - subtitleBudget.lowSince < 30_000) return;
    const current = Number.isFinite(Number(targetDelaySeconds)) ? Number(targetDelaySeconds) : Number(el("targetDelay").value) || 15;
    const needed = current + Math.max(1, Math.ceil(1.5 - margin));
    subtitleBudget.suggested = Math.max(11, Math.min(60, needed));
    el("applyDelayButton").hidden = false;
    el("applyDelayButton").textContent = `字幕来不及：延迟调到 ${subtitleBudget.suggested} 秒`;
  }

  async function applySuggestedDelay() {
    const suggested = subtitleBudget.suggested;
    if (!Number.isFinite(suggested)) return;
    const button = el("applyDelayButton");
    button.disabled = true;
    try {
      await request("/api/target-delay", { seconds: suggested });
      el("targetDelay").value = String(suggested);
      localStorage.setItem("laglingo.targetDelaySeconds", String(suggested));
      el("message").textContent = `已把目标总延迟调到 ${suggested} 秒。字幕就绪预算现在有余量；负载回落后可手动调回。`;
      subtitleBudget.lowSince = null;
      button.hidden = true;
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  }

  async function refreshSubtitles() {
    if (!subtitlePrefs.enabled) return;
    try {
      // 单调序列号游标（redesign Fix I）：时钟绝不参与轮询进度。
      const data = await request(`/api/subtitles?afterSeq=${subtitleAfterSeq}`);
      for (const cue of data.cues || []) {
        const merge = window.mergeSubtitleCueBySeq || ((cues, incoming) => {
          const current = cues.get(incoming.id);
          if (!current || Number(incoming.seq) > Number(current.seq)) cues.set(incoming.id, incoming);
        });
        merge(subtitleCues, cue);
      }
      const maxSeq = Number(data.maxSeq);
      if (Number.isFinite(maxSeq)) subtitleAfterSeq = Math.max(subtitleAfterSeq, maxSeq);
      // 保留期裁剪以已知 cue 的时间线为基准，不用 Date.now()（RC-6）。
      let newest = subtitleMaxKnownEnd;
      for (const cue of subtitleCues.values()) newest = Math.max(newest, cue.tEnd + cue.hold);
      subtitleMaxKnownEnd = newest;
      const cutoff = newest - 125;
      for (const [id, cue] of subtitleCues) {
        if (cue.tEnd + cue.hold < cutoff) subtitleCues.delete(id);
      }
    } catch (error) {
      if (!String(error.message).includes("Failed to fetch")) console.warn("subtitle poll failed", error);
    }
  }

  function playingWallClock() {
    const playing = hls?.playingDate;
    if (playing instanceof Date && Number.isFinite(playing.getTime())) return playing.getTime() / 1000;
    const fragments = latestLevelDetails?.fragments || [];
    const fragment = fragments.find((item) => video.currentTime >= item.start && video.currentTime <= item.start + item.duration);
    const programDateTime = Number(fragment?.programDateTime);
    return fragment && Number.isFinite(programDateTime) ? programDateTime / 1000 + (video.currentTime - fragment.start) : null;
  }

  function renderSubtitle() {
    const layer = el("subtitleLayer");
    if (!subtitlePrefs.enabled || !subtitleScheduler) { clearSubtitle(); return; }
    const wall = playingWallClock();
    if (!Number.isFinite(wall)) { clearSubtitle(); return; }
    const t = wall + subtitlePrefs.offset;
    // 显示门控 = 译文就绪（redesign Fix D）：只有 done/failed 的 cue 才可能被
    // 调度器返回，显示窗口锚在句尾 [tEnd, tEnd+hold]；迟到 cue 按策略追赶或
    // 丢弃。没有「翻译中…」占位，未就绪的 cue 永不上屏。
    const cue = subtitleScheduler.pick([...subtitleCues.values()], t);
    if (!cue) { clearSubtitle(); return; }
    layer.classList.remove("off");
    layer.querySelector(".subtitle-zh").textContent = cue.zh || "";
    layer.querySelector(".subtitle-src").textContent = cue.src || "";
  }

  function clearSubtitle() {
    const layer = el("subtitleLayer");
    layer.classList.add("off");
    layer.querySelector(".subtitle-zh").textContent = "";
    layer.querySelector(".subtitle-src").textContent = "";
  }

  function applySubtitlePrefs() {
    el("subtitlesEnabled").checked = subtitlePrefs.enabled;
    el("subtitleMode").value = subtitlePrefs.mode;
    el("subtitleSize").value = subtitlePrefs.size;
    el("subtitleOffset").value = String(subtitlePrefs.offset);
    el("subtitleOffsetValue").textContent = `${subtitlePrefs.offset.toFixed(1)}s`;
    el("subtitleLayer").dataset.mode = subtitlePrefs.mode;
    el("subtitleLayer").dataset.size = subtitlePrefs.size;
    const windowPrefs = subtitleWindow?.preferences();
    if (windowPrefs) {
      el("subtitleOpacity").value = String(windowPrefs.opacity);
      el("subtitleScale").value = String(windowPrefs.scale);
      el("subtitleSourceColor").value = windowPrefs.sourceColor;
      el("subtitleTranslationColor").value = windowPrefs.translationColor;
    }
  }

  function updateSubtitleWindowStyle() {
    subtitleWindow?.updateStyle({
      opacity: Number(el("subtitleOpacity").value),
      scale: Number(el("subtitleScale").value),
      sourceColor: el("subtitleSourceColor").value,
      translationColor: el("subtitleTranslationColor").value,
    });
  }

  function beginSubtitleDrag(event) {
    if (!subtitleWindow || event.button !== 0) return;
    event.preventDefault();
    const move = (pointer) => {
      const rect = stage.getBoundingClientRect();
      subtitleWindow.moveTo((pointer.clientX - rect.left) / rect.width, (pointer.clientY - rect.top) / rect.height);
    };
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end, { once: true });
  }

  function showError(error) {
    setState("错误", "error");
    el("message").textContent = error.message || String(error);
  }

  function setBusy(busy) {
    el("probe").disabled = busy;
    el("start").disabled = busy || el("quality").disabled;
    el("url").disabled = busy;
    el("targetDelay").disabled = busy;
  }

  el("probe").addEventListener("click", probe);
  el("start").addEventListener("click", start);
  el("stop").addEventListener("click", stop);
  el("applyDelayButton").addEventListener("click", applySuggestedDelay);
  el("toggleFullscreen").addEventListener("click", () => subtitleWindow?.toggleFullscreen());
  el("subtitleDragHandle").addEventListener("pointerdown", beginSubtitleDrag);
  el("subtitleDragHandle").addEventListener("keydown", (event) => {
    if (subtitleWindow?.nudge(event.key, event.shiftKey)) event.preventDefault();
  });
  el("resetSubtitlePosition").addEventListener("click", () => { subtitleWindow?.reset(); applySubtitlePrefs(); });
  ["subtitleOpacity", "subtitleScale", "subtitleSourceColor", "subtitleTranslationColor"].forEach((id) => el(id).addEventListener("input", updateSubtitleWindowStyle));
  document.addEventListener("fullscreenchange", () => subtitleWindow?.apply());
  window.addEventListener("resize", () => subtitleWindow?.apply());
  if (window.ResizeObserver && subtitleWindow) new ResizeObserver(() => subtitleWindow.apply()).observe(stage);
  el("openModelSettings").addEventListener("click", openModelSettings);
  el("closeModelSettings").addEventListener("click", closeModelSettings);
  el("cancelModelSettings").addEventListener("click", closeModelSettings);
  el("openCookieImport").addEventListener("click", openCookieImport);
  el("closeCookieImport").addEventListener("click", closeCookieImport);
  el("cancelCookieImport").addEventListener("click", closeCookieImport);
  el("cookiePlatform").addEventListener("change", updateCookiePlatformHelp);
  el("cookieImportForm").addEventListener("submit", submitCookieImport);
  el("modelSettingsForm").addEventListener("submit", saveModelSettings);
  el("addAsrProfile").addEventListener("click", () => addProvider("asr"));
  el("addTranslationProfile").addEventListener("click", () => addProvider("translation"));
  el("subtitlesEnabled").addEventListener("change", () => { subtitlePrefs.enabled = el("subtitlesEnabled").checked; localStorage.setItem("laglingo.subtitle.enabled", String(subtitlePrefs.enabled)); if (!subtitlePrefs.enabled) clearSubtitle(); });
  el("subtitleMode").addEventListener("change", () => { subtitlePrefs.mode = el("subtitleMode").value; localStorage.setItem("laglingo.subtitle.mode", subtitlePrefs.mode); applySubtitlePrefs(); });
  el("subtitleSize").addEventListener("change", () => { subtitlePrefs.size = el("subtitleSize").value; localStorage.setItem("laglingo.subtitle.size", subtitlePrefs.size); applySubtitlePrefs(); });
  el("subtitleOffset").addEventListener("input", () => { subtitlePrefs.offset = Number(el("subtitleOffset").value); localStorage.setItem("laglingo.subtitle.offset", String(subtitlePrefs.offset)); applySubtitlePrefs(); });
  el("url").addEventListener("keydown", (event) => { if (event.key === "Enter") probe(); });
  video.addEventListener("playing", () => setState("延迟播放中", "stable"));
  video.addEventListener("waiting", () => setState("播放器缓冲", "waiting"));
  applySubtitlePrefs();
  subtitleWindow?.apply();
  loadSubtitleDefaults();
  statusTimer = setInterval(refreshStatus, 1000);
  subtitleTimer = setInterval(refreshSubtitles, 500);
  subtitleRenderTimer = setInterval(renderSubtitle, 100);
  refreshStatus();
  window.addEventListener("beforeunload", () => { clearInterval(statusTimer); clearInterval(subtitleTimer); clearInterval(subtitleRenderTimer); });
})();
