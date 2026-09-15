(() => {
  const el = (id) => document.getElementById(id);
  const video = el("video");
  const stage = document.querySelector(".player-stage");
  const state = document.querySelector(".state-badge");
  const liveChip = el("liveChip");
  let hls = null;
  let lastPlaylistUrl = null;
  let browserLatency = null;
  const initialParams = new URLSearchParams(location.search);
  let authToken = initialParams.get("authToken");
  let controlsBusy = false;
  let sessionAction = null;
  let lastSessionState = "idle";
  let statusPoller = null;
  let subtitlePoller = null;
  let diagnosticsPoller = null;
  let subtitleRenderTimer = null;
  let latestLevelDetails = null;
  let sourceWasStalled = false;
  let lastRecoverySeekAt = -Infinity;
  // Fatal-error recovery attempts since playback last made progress. hls.js's
  // media-error recovery is a full SourceBuffer rebuild, so an uncapped loop of
  // them is indistinguishable from "the video keeps stuttering".
  let mseRecoveryAttempts = 0;
  let mseRecoveryProgressAt = 0;
  let subtitleCues = new Map();
  let subtitleAfterSeq = 0;
  let subtitleMaxKnownEnd = 0;
  // Advance by one 100ms render tick plus the measured 50ms median residual.
  // This centers normal cue onset without increasing timer or polling work.
  const SUBTITLE_RENDER_ADVANCE_SECONDS = 0.15;
  let providerCatalog = null;
  let roleCatalog = null;
  let editingSection = "asr";
  const editingModel = {};

  /* ── 运行诊断 ──────────────────────────────────────────────
     渲染进程里每一个用户可见的报错都从 showError() 出去，所以那一个钩子就
     覆盖了大部分故障；后端自己的记录走 /api/logs 轮询合并进来。两者共用一条
     时间线：桌面后端就在本机，时钟一致。 */
  const diagnosticsLog = window.LingerLensDiagnostics?.createDiagnosticsLog() || null;
  let diagnosticsClient = null;
  let diagnosticsBar = null;
  let updateClient = null;
  let buildIdentity = null;

  /* 版本与构建时间：这两样合起来才能回答「我手上这份是不是最新」。 */
  function describeBuild(data) {
    if (!data?.version) return null;
    const stamp = data.builtAt ? new Date(data.builtAt) : null;
    const when = stamp && !Number.isNaN(stamp.getTime())
      ? `${stamp.getFullYear()}-${String(stamp.getMonth() + 1).padStart(2, "0")}-${String(stamp.getDate()).padStart(2, "0")} `
        + `${String(stamp.getHours()).padStart(2, "0")}:${String(stamp.getMinutes()).padStart(2, "0")}`
      : "—";
    return { version: data.version, builtAt: when, packaged: Boolean(data.packaged) };
  }

  function diagnosticsContext() {
    const label = (key, fallback) => window.I18N?.t(key, null, fallback) || fallback;
    return {
      [label("diag.ctx.locale", "界面语言")]: window.I18N?.current || document.documentElement.lang || "zh-CN",
      [label("diag.ctx.state", "会话状态")]: lastSessionState,
      [label("diag.ctx.target", "目标延迟")]: `${el("targetDelay")?.value ?? "—"}s`,
      [label("diag.ctx.build", "构建")]: buildIdentity
        ? `${buildIdentity.version} · ${buildIdentity.builtAt} · ${buildIdentity.packaged ? "packaged" : "dev"}` : "—",
      [label("diag.ctx.devlog", "开发日志")]: describeDevLog(),
      [label("diag.ctx.page", "页面")]: location.href,
      [label("diag.ctx.useragent", "用户代理")]: navigator.userAgent,
    };
  }

  /* 报告里带上开发日志的去向：这样一份贴出来的报告自己就说清了它是不是
     也落了盘，以及落在哪里。没配就返回空串，报告里不会多出一行噪音。 */
  function describeDevLog() {
    const state = devLogClient?.get();
    if (!state) return "";
    if (state.error) return `${updateLabel("diag.devlog.failed", "日志写入失败", {})} — ${state.error}`;
    if (state.enabled && state.file) return state.file;
    return "";
  }

  async function copyDiagnostics() {
    if (!diagnosticsLog) return;
    const text = window.LingerLensDiagnostics.formatDiagnostics({
      log: diagnosticsLog,
      title: window.I18N?.t("diag.report.title", null, "LingerLens 诊断信息") || "LingerLens 诊断信息",
      timeLabel: window.I18N?.t("diag.report.time", null, "生成时间") || "生成时间",
      countLabel: window.I18N?.t("diag.report.count", null, "记录") || "记录",
      levelLabels: {
        info: window.I18N?.t("diag.level.info", null, "信息") || "信息",
        warn: window.I18N?.t("diag.level.warn", null, "警告") || "警告",
        error: window.I18N?.t("diag.level.error", null, "错误") || "错误",
      },
      context: diagnosticsContext(),
    });
    const button = el("diagCopy");
    let copied = false;
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
    } catch {
      // 剪贴板 API 在非安全上下文里会拒绝；退回到一个临时 textarea。
      const scratch = document.createElement("textarea");
      scratch.value = text;
      scratch.setAttribute("readonly", "");
      scratch.style.position = "fixed";
      scratch.style.opacity = "0";
      document.body.append(scratch);
      scratch.select();
      try { copied = document.execCommand("copy"); } catch { copied = false; }
      scratch.remove();
    }
    if (!button) return;
    const original = button.textContent;
    button.textContent = copied
      ? window.I18N?.t("diag.copied", null, "已复制") || "已复制"
      : window.I18N?.t("diag.copyFailed", null, "复制失败") || "复制失败";
    setTimeout(() => { button.textContent = original; }, 1600);
  }

  diagnosticsClient = window.LingerLensDiagnostics?.createDiagnosticsClient({
    onUpdate: (data) => {
      if (!diagnosticsLog || !data.ok) return;
      // sessionId 变了说明后端换了进程，seq 空间从头开始：必须清空去重表并
      // 从 0 重新拉，否则新记录会被当成已见过的丢掉。
      if (data.sessionId && diagnosticsLog.getSessionId() !== null && data.sessionId !== diagnosticsLog.getSessionId()) {
        diagnosticsLog.clear();
        diagnosticsClient.resetForRestart();
        diagnosticsBar?.render();
        return;
      }
      if (data.sessionId) diagnosticsLog.setSessionId(data.sessionId);
      if (data.missed > 0) {
        diagnosticsBar?.push("warn", "backend", `后端日志环形缓冲已覆盖 ${data.missed} 条较早记录`);
      }
      diagnosticsLog.merge(data.records);
      diagnosticsBar?.render();
    },
  }) || null;
  diagnosticsBar = window.LingerLensDiagnostics?.createDiagnosticsBar({
    log: diagnosticsLog,
    onCopy: copyDiagnostics,
  }) || null;

  /* ── 开发日志落盘 ────────────────────────────────────────────
     只有主进程能写文件（渲染进程在沙箱里），所以记录经 lingerlens:// 交给它。
     没配日志文件时 status() 回来的 enabled 是 false，之后一条都不发。 */
  const devLogClient = window.LingerLensDiagnostics?.createDevLogClient() || null;
  /* 游标用插入序 order，不用数组下标也不用时间戳：环形缓冲会淘汰旧记录，而且
     后端记录的时间戳可能落在已经交出去的记录之前，两者都会漏。 */
  let devLogCursor = 0;
  let devLogAlerted = false;

  async function flushDevLog() {
    if (!devLogClient || !diagnosticsLog || !devLogClient.get()?.enabled) return 0;
    const pending = diagnosticsLog.list()
      .filter((entry) => entry.order > devLogCursor)
      .sort((a, b) => a.order - b.order);
    if (!pending.length) return 0;
    const written = await devLogClient.send(window.LingerLensDiagnostics.formatRecordLines(pending));
    // 主进程每次有上限，只接受前 N 条；游标只能推进到真正落盘的那一条，
    // 否则剩下的记录就永久丢了。
    if (written > 0) devLogCursor = pending[Math.min(written, pending.length) - 1].order;
    return written;
  }

  function renderDevLogState(state) {
    const active = Boolean(state?.active) && !state?.error;
    const toggle = el("diagLogToggle");
    if (toggle) {
      toggle.textContent = active
        ? updateLabel("diag.log.stop", "停止记录", {})
        : updateLabel("diag.log.record", "记录日志到文件", {});
      // 说清楚代价再让人点：日志里可能有私有直播地址。
      toggle.title = updateLabel("diag.log.private", "日志可能包含私有直播地址，分享前先看一眼。", {});
      toggle.disabled = Boolean(state?.error);
    }
    const open = el("diagLogOpen");
    if (open) open.hidden = !active;

    const node = el("diagLogState");
    if (node) {
      if (state?.error) {
        node.hidden = false;
        node.dataset.tone = "error";
        node.textContent = updateLabel("diag.devlog.failed", "日志写入失败", {});
        node.title = `${state.file || ""}\n${state.error}`.trim();
      } else if (state?.enabled && state.file) {
        node.hidden = false;
        node.dataset.tone = "on";
        node.textContent = updateLabel("diag.devlog.on", "记录中 {file}", { file: state.file.split(/[\\/]/).pop() });
        node.title = state.file;
      } else {
        node.hidden = true;
        node.removeAttribute("title");
      }
    }
    // 要求了文件却没写成——这是必须说出来的一种失败，因为不说的话整个
    // 实测过程会安静地产出零字节，而人要过一小时才会发现。
    if (state?.error && !devLogAlerted) {
      devLogAlerted = true;
      diagnosticsBar?.push("warn", "devlog",
        `${updateLabel("diag.devlog.failed", "日志写入失败", {})}：${state.error}`);
      diagnosticsBar?.render();
    }
  }

  async function initDevLog() {
    if (!devLogClient) return;
    renderDevLogState(await devLogClient.status());
  }

  /*
     开始记录时把游标归零，让**已经发生**的错误也补写进去。用户是被某个故障
     逼来点这个按钮的，只记下点击之后的事情等于什么都没帮上。
  */
  async function toggleDevLog() {
    if (!devLogClient) return;
    const wasActive = Boolean(devLogClient.get()?.active);
    const state = wasActive ? await devLogClient.stop() : await devLogClient.start();
    devLogAlerted = false;
    if (!wasActive && state?.enabled) {
      devLogCursor = 0;
      await flushDevLog();
      diagnosticsBar?.render();
    }
    renderDevLogState(state);
  }

  /* ── 更新 ──────────────────────────────────────────────────
     主进程在启动 10 秒后自己查一次；这里只负责把状态显示出来，以及用户按
     下按钮时触发检查/下载。下载期间才轮询进度，其余时候一次都不发。 */
  const updateLabel = (key, fallback, vars) =>
    window.I18N?.t(key, vars, fallback) || Object.entries(vars || {})
      .reduce((text, [name, value]) => text.replace(`{${name}}`, value), fallback);

  let lastUpdateSignature = null;
  let updateProgressTimer = null;

  function stopUpdateProgress() {
    if (updateProgressTimer) clearInterval(updateProgressTimer);
    updateProgressTimer = null;
  }

  function renderUpdate(state) {
    const identity = describeBuild(state);
    if (identity) {
      buildIdentity = identity;
      const node = el("diagBuild");
      if (node) {
        node.textContent = `${identity.version} · ${identity.builtAt}`;
        node.title = `${identity.version} · ${identity.builtAt} · ${identity.packaged ? "packaged" : "dev"}`;
      }
    }
    const update = state?.update || {};
    const version = update.update?.version || "";
    const signature = `${update.status}:${version}:${update.error || ""}`;
    const button = el("diagUpdate");
    if (!button) return;
    if (signature === lastUpdateSignature) {
      // Progress changes far more often than status; only the label moves.
      if (update.status === "downloading" && update.progress) {
        const percent = Math.min(99, Math.round((update.progress.received / Math.max(1, update.progress.total)) * 100));
        button.textContent = updateLabel("diag.update.downloading", `下载中 {percent}%`, { percent });
      }
      return;
    }
    lastUpdateSignature = signature;
    button.hidden = false;
    button.disabled = false;
    stopUpdateProgress();

    if (update.status === "available") {
      button.textContent = updateLabel("diag.update.download", "下载更新 {version}", { version });
      // 模板串里不能写 ${current}：那是 JS 插值，不是 updateLabel 的占位符。
      diagnosticsBar?.push("info", "update",
        updateLabel("diag.update.found", "发现新版本 {version}（当前 {current}）",
          { version, current: state?.version || "" }));
    } else if (update.status === "downloading") {
      button.disabled = true;
      button.textContent = updateLabel("diag.update.downloading", "下载中 {percent}%", { percent: 0 });
      updateProgressTimer = setInterval(() => { void updateClient?.status().then(renderUpdate); }, 1000);
    } else if (update.status === "ready" || update.status === "installing") {
      button.disabled = true;
      button.textContent = updateLabel("diag.update.restarting", "正在重启并安装…");
      diagnosticsBar?.push("info", "update", updateLabel("diag.update.ready", "更新已下载并校验通过，即将重启安装"));
    } else if (update.status === "failed") {
      button.textContent = updateLabel("diag.update.retry", "重试检查更新");
      // 检查失败（离线、公司网络、还没有 release）是常态，不该刷屏。
      if (update.error && update.error !== "no fetch available") {
        diagnosticsBar?.push("warn", "update", updateLabel("diag.update.failed", "检查更新失败：{error}", { error: update.error }));
      }
    } else if (update.status === "current") {
      button.textContent = updateLabel("diag.update.current", "已是最新版本");
    } else {
      button.textContent = updateLabel("diag.update.check", "检查更新");
    }
  }

  updateClient = window.LingerLensDiagnostics?.createUpdateClient({ onUpdate: renderUpdate }) || null;
  el("diagUpdate")?.addEventListener("click", async () => {
    const status = updateClient?.get()?.update?.status;
    if (status === "available") await updateClient.install();
    else await updateClient.check();
    renderUpdate(updateClient?.get());
  });
  void updateClient?.status().then(renderUpdate);
  /* 先问一次开发日志有没有配、能不能写，再决定后面发不发记录。 */
  void initDevLog();
  el("diagLogToggle")?.addEventListener("click", () => { void toggleDevLog(); });
  el("diagLogOpen")?.addEventListener("click", () => { void devLogClient?.reveal(); });
  /* 按钮标签是 JS 按状态拼的，语言切换时 i18n 那套反向查表管不到它。 */
  document.addEventListener("i18n:changed", () => renderDevLogState(devLogClient?.get()));
  function providerHasCredential(provider) {
    return provider?.apiKeyConfigured === true
      || Boolean(provider?.apiKey && provider.apiKey !== "***");
  }

  function providerOption(provider, selected = false) {
    const label = provider.label || provider.model || provider.id;
    const detail = provider.model && !String(provider.label || "").includes(provider.model)
      ? ` · ${provider.model}`
      : "";
    return `<option value="${escapeHtml(provider.id)}"${selected ? " selected" : ""}>${escapeHtml(label)}${escapeHtml(detail)}</option>`;
  }

  function roleProviders(group, references = []) {
    const referenced = new Set(references.filter(Boolean));
    return (group?.providers || []).filter((provider) => providerHasCredential(provider) || referenced.has(provider.id));
  }

  function renderRoleSelectors(config) {
    roleCatalog = config;
    const translation = config.translation || { providers: [] };
    const fallback = Array.isArray(translation.fallback) ? translation.fallback : [];
    const chatActive = config.chatTranslation?.active || translation.active;
    const definitions = [
      ["roleAsr", config.asr, config.asr?.active, [config.asr?.active]],
      ["roleSubtitle", translation, translation.active, [translation.active]],
      ["roleChat", translation, chatActive, [chatActive, translation.active]],
    ];
    for (const [id, group, active, references] of definitions) {
      el(id).innerHTML = roleProviders(group, references).map((provider) => providerOption(provider, provider.id === active)).join("");
    }
    const fallbackSelect = el("roleFallback");
    if (fallbackSelect) {
      const fallbackActive = fallback.find((id) => id !== translation.active) || "";
      // 和上面三个选择器用同一条规则、同一个列表。以前这里额外把当前生效的
      // Provider 滤掉，于是只配了一个翻译 Provider 时兜底永远是空的——那不像
      // 「没得选」，更像坏掉了。
      const fallbackProviders = roleProviders(translation, [fallbackActive, translation.active]);
      fallbackSelect.innerHTML = `<option value="">不选</option>${fallbackProviders.map((provider) => providerOption(provider, provider.id === fallbackActive)).join("")}`;
    }
  }
  async function selectRole(id, section) {
    const controls = ["roleAsr", "roleSubtitle", "roleFallback", "roleChat"].map(el);
    controls.forEach(c => c.disabled = true);
    el("roleFeedback").textContent = "正在应用选择…";
    try {
      const value = el(id).value;
      const patch = section === "translationFallback"
        ? { translation: { fallback: value ? [value] : [] } }
        : { [section]: { active: value } };
      const data = await request("/api/providers", patch);
      renderRoleSelectors(data);
      el("roleFeedback").textContent = section === "asr" ? "已保存" : section === "translationFallback" ? "已保存兜底选择" : "已应用";
    } catch (error) {
      if (roleCatalog) renderRoleSelectors(roleCatalog);
      el("roleFeedback").textContent = `切换失败：${error.message}`;
    } finally { controls.forEach(c => c.disabled = false); }
  }
  // Language catalog/capability state from /api/languages (server-authoritative)
  // plus the persisted subtitle preferences used as the persistence merge base.
  const languageState = { asr: null, translation: null, subtitle: null, candidates: [] };
  let sourceSelector = null;
  let targetSelector = null;
  let candidateSelector = null;
  const subtitleScheduler = window.createSubtitleScheduler
    ? window.createSubtitleScheduler({ minDwell: 1.2, maxLateSeconds: 2.0, bridgeGap: 0.3 })
    : null;
  const subtitleWindow = window.createSubtitleWindowController
    ? window.createSubtitleWindowController({ stage, windowElement: el("subtitleLayer"), storage: localStorage, fullscreenDocument: document })
    : null;
  const mediaClock = window.createMediaClock
    ? window.createMediaClock({
        hlsProvider: () => hls,
        videoElement: video,
        levelDetailsProvider: () => latestLevelDetails,
      })
    : null;
  const liveMessagesClient = window.createLiveMessagesClient
    ? window.createLiveMessagesClient({
        request,
        onUpdate: (data) => updateLiveMessagesUI(data),
      })
    : null;
  const filterPureEmojiPreference = localStorage.getItem("lingerlens_filter_pure_emoji") === "true";
  const chatOverlay = window.createChatOverlay({ container: el("chatOverlay") });
  const chatOverlayToggle = el("chatOverlayToggle");
  const chatOverlayOpacity = el("chatOverlayOpacity");
  const chatOverlaySize = el("chatOverlaySize");
  chatOverlayToggle.checked = localStorage.getItem("lingerlens.chatOverlay.enabled") !== "false";
  chatOverlayOpacity.value = localStorage.getItem("lingerlens.chatOverlay.opacity") || "0.8";
  chatOverlaySize.value = localStorage.getItem("lingerlens.chatOverlay.size") || "1";
  const applyChatOpacity = () => el("chatOverlay").style.setProperty("--chat-opacity", chatOverlayOpacity.value);
  applyChatOpacity();
  el("chatOverlay").style.setProperty("--chat-size", chatOverlaySize.value);
  chatOverlayToggle.addEventListener("change", () => {
    localStorage.setItem("lingerlens.chatOverlay.enabled", String(chatOverlayToggle.checked));
    chatOverlay.clear();
  });
  chatOverlayOpacity.addEventListener("input", () => {
    applyChatOpacity();
    localStorage.setItem("lingerlens.chatOverlay.opacity", chatOverlayOpacity.value);
  });
  chatOverlaySize.addEventListener("change", () => {
    localStorage.setItem("lingerlens.chatOverlay.size", chatOverlaySize.value);
  });
  const liveMessagesTimeline = window.createLiveMessagesTimeline
    ? window.createLiveMessagesTimeline({
        container: el("chatTimelineList"),
        statsContainer: el("chatStatsIndicator"),
        source: () => liveMessagesClient ? [...liveMessagesClient.getStore().values()] : [],
        filterPureEmoji: filterPureEmojiPreference,
      })
    : null;
  const workbenchController = window.createWorkbenchController
    ? window.createWorkbenchController({
        container: el("workbenchContainer"),
        tabs: {
          split: el("tabWorkbenchSplit"),
          subtitles: el("tabWorkbenchSubtitles"),
          chat: el("tabWorkbenchChat"),
        },
        storage: localStorage,
      })
    : null;
  const paneResizer = window.createPaneResizer
    ? window.createPaneResizer({
        workbench: el("workbenchContainer"),
        storage: localStorage,
        panes: { subtitles: el("paneSubtitles"), chat: el("paneChat") },
      })
    : null;
  const followSubtitlesController = window.createFollowModeController
    ? window.createFollowModeController({
        container: el("subtitlesTimelineList"),
        button: el("followSubtitlesBtn"),
      })
    : null;
  const followChatController = window.createFollowModeController
    ? window.createFollowModeController({
        container: el("chatTimelineList"),
        button: el("followChatBtn"),
      })
    : null;

  let timelineRenderTimer = null;
  const subtitleBudget = { lowSince: null, suggested: null };
  const savedTargetDelay = Number(localStorage.getItem("lingerlens.targetDelaySeconds") || 15);
  const chatTranslatePreference = localStorage.getItem("lingerlens.liveMessages.translate") === "true";
  el("chatTranslateToggle").checked = chatTranslatePreference;
  if (el("hidePureEmojiToggle")) {
    el("hidePureEmojiToggle").checked = filterPureEmojiPreference;
    el("hidePureEmojiToggle").addEventListener("change", (e) => {
      const enabled = Boolean(e.target.checked);
      localStorage.setItem("lingerlens_filter_pure_emoji", String(enabled));
      if (liveMessagesTimeline && typeof liveMessagesTimeline.setFilterPureEmoji === "function") {
        liveMessagesTimeline.setFilterPureEmoji(enabled);
      }
    });
  }
  const subtitlePrefs = {
    enabled: localStorage.getItem("lingerlens.subtitle.enabled") !== "false",
    mode: localStorage.getItem("lingerlens.subtitle.mode") || "bilingual",
    size: localStorage.getItem("lingerlens.subtitle.size") || "medium",
    offset: Number(localStorage.getItem("lingerlens.subtitle.offset") || 0),
  };
  if (initialParams.get("url")) el("url").value = initialParams.get("url");
  if (el("proxy")) el("proxy").value = localStorage.getItem("lingerlens.proxy") || "";
  el("targetDelay").value = String(savedTargetDelay > 10 && savedTargetDelay <= 60 ? savedTargetDelay : 15);

  const setState = (text, tone = "idle") => {
    el("stateText").textContent = text;
    state.dataset.tone = tone;
    // 顶栏 LIVE 徽章跟随会话状态：只有真正在跑直播时才亮。
    if (liveChip) liveChip.hidden = tone === "idle" || tone === "error";
  };

  const seconds = (value) => Number.isFinite(value) ? `${value.toFixed(1)} 秒` : "—";
  const integer = (value) => Number(value || 0).toLocaleString("zh-CN");
  const cny = (value, reason) => Number.isFinite(Number(value))
    ? `¥${Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 4, maximumFractionDigits: 6 })}`
    : `不可估算${reason ? `（${reason}）` : ""}`;

  async function request(path, body) {
    const timeout = path === "/api/probe" ? 20000 : 30000;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    let response;
    try {
      response = await Promise.race([
        fetch(path, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
        signal: controller.signal,
        }),
        new Promise((_, reject) => setTimeout(() => reject(new Error(
          path === "/api/probe"
            ? "读取直播信息超时（20 秒）。请确认代理软件正在运行，或检查直播是否需要登录 Cookie。"
            : "本地后台响应超时，请重试。"
        )), timeout)),
      ]);
    } catch (error) {
      if (error?.name === "AbortError") {
        throw new Error(path === "/api/probe"
          ? "读取直播信息超时。请确认链接是正在进行的公开直播，并检查网络、登录状态或代理设置。"
          : "本地后台响应超时，请重试。");
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
    const text = await response.text();
    let payload = {};
    try { payload = text ? JSON.parse(text) : {}; } catch { payload = { error: text }; }
    const contentType = response.headers.get("content-type") || "";
    if (!response.ok) {
      const detail = contentType.includes("json") && payload.error
        ? payload.error
        : `本地后台接口 ${path} 返回 HTTP ${response.status}，请确认 LingerLens 后台已启动。`;
      throw new Error(detail);
    }
    if (!contentType.includes("json")) {
      throw new Error(`本地后台接口 ${path} 返回了非 JSON 响应，请重启 LingerLens。`);
    }
    return payload;
  }

  function commonBody() {
    const body = { url: el("url").value.trim() };
    const proxy = el("proxy")?.value.trim() || "";
    if (proxy) { body.proxy = proxy; localStorage.setItem("lingerlens.proxy", proxy); }
    if (authToken) body.authToken = authToken;
    return body;
  }

  async function probe() {
    if (controlsBusy || sessionAction || lastSessionState === "running") return;
    if (!el("url").value.trim() || !el("url").checkValidity()) {
      el("setupFeedback").textContent = "请先粘贴完整的直播链接。";
      el("url").focus();
      return;
    }
    el("quality").disabled = true;
    el("setupPlayback").hidden = true;
    el("probe").textContent = "准备中…";
    el("probe").setAttribute("aria-busy", "true");
    el("setupFeedback").textContent = "正在读取直播信息和可用清晰度…";
    setBusy(true);
    setState("正在准备", "waiting");
    el("message").textContent = "正在准备直播…";
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
      const preferred = (typeof pickPreferredQuality === "function" ? pickPreferredQuality(data.qualities) : null)
        || data.qualities.find((quality) => quality.height === 1080 && !quality.requiresTranscode)
        || data.qualities.find((quality) => quality.height === 720 && !quality.requiresTranscode);
      select.value = preferred?.qualityId || "auto";
      select.disabled = false;
      el("start").disabled = false;
      el("streamTitle").textContent = data.title || "直播格式已就绪";
      el("setupPlayback").hidden = false;
      el("setupFeedback").textContent = "准备好了，选择清晰度后点击启动。";
      el("message").textContent = "直播已就绪。";
      setState("准备就绪", "active");
    } catch (error) {
      showError(error);
    } finally {
      el("probe").textContent = "准备";
      el("probe").removeAttribute("aria-busy");
      setBusy(false);
    }
  }

  async function start() {
    setSessionAction("starting");
    setMediaLoading(true, "正在启动直播…");
    setBusy(true);
    setState("启动合流", "waiting");
    destroyPlayer();
    try {
      if (el("subtitlesEnabled").checked) {
        const problem = validateLanguageSettingsClient();
        if (problem) throw new Error(problem);
        await persistLanguageSettings();
      }
      const body = {
        ...commonBody(),
        qualityId: el("quality").value,
        targetDelaySeconds: Number(el("targetDelay").value),
        subtitles: {
          enabled: el("subtitlesEnabled").checked,
          sourceLanguage: sourcePolicyFromUi(),
          targetLanguage: targetSelector?.value || "zh-Hans",
        },
        liveMessages: {
          enabled: true,
          translate: el("chatTranslateToggle").checked,
        },
      };
      const data = await request("/api/start", body);
      lastSessionState = data.status?.state || "running";
      localStorage.setItem("lingerlens.targetDelaySeconds", String(body.targetDelaySeconds));
      el("stop").disabled = false;
      const quality = data.quality;
      el("resolution").textContent = `${quality.width || "?"}×${quality.height || "?"}${quality.fps ? ` @ ${quality.fps}fps` : ""}`;
      el("message").textContent = "正在建立播放缓冲，画面就绪后自动播放。";
      setState("建立延迟缓冲", "waiting");
      setMediaLoading(true, "正在建立直播缓冲…");
      authToken = null;
    } catch (error) {
      lastSessionState = "idle";
      setMediaLoading(false);
      showError(error);
    } finally {
      setSessionAction(null);
      setBusy(false);
    }
  }

  function resetStoppedUi() {
    destroyPlayer();
    lastSessionState = "idle";
    setSessionAction(null);
    setMediaLoading(false);
    stage.classList.remove("has-media");
    el("quality").disabled = true;
    el("quality").innerHTML = '<option value="auto">自动（最高兼容）</option>';
    el("start").disabled = true;
    el("setupPlayback").hidden = true;
    el("setupFeedback").textContent = "已停止。可以重新准备，或粘贴另一场直播的链接。";
    el("stop").disabled = true;
    el("streamTitle").textContent = "等待直播地址";
    el("message").textContent = "已停止当前直播。";
    for (const id of ["hiddenDelay", "playerDelay", "totalDelay", "buffer", "resolution", "uptime", "subtitleProviderStatus", "asrUsageCost", "translationUsageCost", "totalUsageCost", "translationLatency", "subtitleReadyLag", "budgetMargin", "cueDuration", "timingSources", "schedulerDrops"]) {
      el(id).textContent = "—";
    }
    setState("已停止", "idle");
  }

  async function stop() {
    setSessionAction("stopping");
    setMediaLoading(true, "正在停止并清理本地会话…");
    setBusy(true);
    setState("正在停止", "waiting");
    try {
      await request("/api/stop", {});
      resetStoppedUi();
    } catch (error) {
      setMediaLoading(false);
      showError(error);
    } finally {
      setSessionAction(null);
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
        // 用「秒」而不是「分片个数」表达延迟。分片时长跟随源 GOP（FFmpeg 不在
        // 关键帧之间切分），可能是 2s 或 5s；按个数配置会让播放延迟随分片时长
        // 静默翻倍。hls.js 中 liveSyncDuration 优先于 liveSyncDurationCount
        // （vendor/hls.min.js: targetLatency = void 0 !== liveSyncDuration
        //   ? liveSyncDuration : liveSyncDurationCount * targetduration）。
        //
        // 播放头到公开直播边缘的距离 *就是* 浏览器能攒下的前向缓冲：直播流里
        // 边缘之后没有媒体可下。坐在边缘后 3 秒 = 只有 3 秒缓冲，任何一次分片
        // 拉取变慢或解码卡顿都会立刻耗尽它，表现为进度条反复卡住。
        // 加大服务端的 publish delay 救不了这一点 —— 公开边缘会同步前移，
        // 播放头依然只落后边缘 3 秒。缓冲必须在播放器这一侧要。
        // 必须与服务端 PLAYER_LIVE_SYNC_SECONDS 一致（由 test_web_assets.js 断言）。
        liveSyncDuration: 12,
        // 必须显著大于 liveSyncDuration，否则 hls.js 会不停判定「延迟超标」
        // 并强制前跳，那本身就是卡顿感的来源。170s 配合服务端 180s 公开窗口，
        // 用户可以自由回看而不被强拖回直播边缘。
        liveMaxLatencyDuration: 170,
        maxBufferLength: 90,
        // 回拖缓冲 ≈ 3 分钟（180s 公开窗口 + 余量）。
        backBufferLength: 190,
        enableWorker: true,
      });
      const player = hls;
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
        // Ignore errors from a player instance this session has already replaced.
        if (hls !== player) return;
        const errorType = data.type === Hls.ErrorTypes.NETWORK_ERROR ? "network"
          : data.type === Hls.ErrorTypes.MEDIA_ERROR ? "media"
          : null;
        if (errorType === null) {
          showError(new Error(`hls.js fatal: ${data.details}`));
          return;
        }
        const decision = window.decideMseErrorRecovery({
          errorType,
          attemptsInWindow: mseRecoveryAttempts,
        });
        if (decision.action === "give-up") {
          showError(new Error(`播放无法恢复：${decision.reason}（${data.details}）`));
          return;
        }
        mseRecoveryAttempts += 1;
        // Rebuild/reload always costs a rebuffer, so space attempts out instead
        // of re-entering the failure immediately.
        window.setTimeout(() => {
          if (!hls || hls !== player) return;
          if (decision.action === "recover-media") hls.recoverMediaError();
          else if (decision.action === "swap-codec") hls.swapAudioCodec();
          else hls.startLoad();
        }, decision.delayMs);
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
    video.play().catch(() => {
      if (sessionAction === "stopping") return;
      setMediaLoading(false);
      setState("待播放", "waiting");
      updatePlayerControls();
      revealPlayerControls();
    });
  }

  function destroyPlayer() {
    if (hls) hls.destroy();
    hls = null;
    lastPlaylistUrl = null;
    browserLatency = null;
    latestLevelDetails = null;
    sourceWasStalled = false;
    lastRecoverySeekAt = -Infinity;
    mseRecoveryAttempts = 0;
    mseRecoveryProgressAt = 0;
    video.playbackRate = 1;
    subtitleCues.clear();
    subtitleAfterSeq = 0;
    subtitleMaxKnownEnd = 0;
    if (subtitleScheduler) subtitleScheduler.reset();
    if (liveMessagesClient) liveMessagesClient.reset();
    if (liveMessagesTimeline) liveMessagesTimeline.clear();
    chatOverlay.clear();
    clearSubtitle();
    renderSubtitlesTimeline(null);
    video.removeAttribute("src");
    video.load();
  }

  async function refreshStatus() {
    try {
      const data = await request("/api/status");
      // The stream may have been started from another tab, the extension, or a
      // diagnostic client. Restore controls from server truth on every poll
      // instead of relying on this page's start() call having run.
      lastSessionState = data.state;
      renderSessionControls();
      if (data.playlistUrl) attach(data.playlistUrl);
      if (data.state === "error" && sessionAction !== "stopping") {
        // The error branch throws, so the shared updateStallOverlay() call near
        // the end of this function never runs. Show the error banner here
        // rather than relying on a second, unexplained call at the top.
        updateStallOverlay(data);
        setMediaLoading(false);
        throw new Error(data.error || "FFmpeg failed");
      }
      if (data.state === "running") {
        const playing = data.playlistReady && !video.paused;
        if (sessionAction !== "stopping") {
          setState(data.playlistReady ? (playing ? "延迟播放中" : "就绪待播放") : "建立延迟缓冲", playing ? "stable" : "waiting");
          if (!data.playlistReady) setMediaLoading(true, "正在建立直播缓冲…");
        }
      }
      if (data.state === "idle" && (hls || video.src) && sessionAction !== "stopping") resetStoppedUi();
      el("hiddenDelay").textContent = seconds(Number(data.hiddenMediaSeconds));
      const playerBehind = Number.isFinite(browserLatency) ? browserLatency : estimateVideoLatency();
      el("playerDelay").textContent = seconds(playerBehind);
      const measuredDelay = Number(data.sourceDelaySeconds || 0) + Number(data.hiddenMediaSeconds || 0) + (playerBehind || 0);
      el("totalDelay").textContent = seconds(measuredDelay);
      updateStallOverlay(data);
      if (data.state === "running" && Number(data.targetDelaySeconds) > 10) {
        el("targetDelay").value = String(data.targetDelaySeconds);
        localStorage.setItem("lingerlens.targetDelaySeconds", String(data.targetDelaySeconds));
      }
      const ahead = bufferAhead();
      el("buffer").textContent = seconds(ahead);
      updatePlaybackRecovery(data, playerBehind, ahead);
      el("uptime").textContent = seconds(Number(data.uptimeSeconds));
      const subtitles = data.subtitles || {};
      el("subtitleProviderStatus").innerHTML = subtitles.asrProviderId
        ? `<span><small>ASR</small>${escapeHtml(subtitles.asrProviderLabel || subtitles.asrProviderId)}</span><span><small>翻译</small>${escapeHtml(subtitles.translationProviderLabel || subtitles.translationProviderId || "仅原文")}</span>`
        : "<span class=\"usage-empty\">未运行</span>";
      const asrUsage = subtitles.asrUsage || { seconds: subtitles.asrSeconds || 0 };
      const translationUsage = subtitles.translationUsage || {};
      el("asrUsageCost").innerHTML = `<span><small>音频</small>${Number(asrUsage.seconds || 0).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} 秒</span><span><small>费用</small>${cny(subtitles.asrEstimatedCostCny, subtitles.asrEstimateReason)}</span>`;
      el("translationUsageCost").innerHTML = `<span><small>输入</small>${integer(translationUsage.nonCachedInputTokens)}</span><span><small>缓存</small>${integer(translationUsage.cachedInputTokens)}</span><span><small>输出</small>${integer(translationUsage.outputTokens)}</span><span class=\"usage-cost\"><small>费用</small>${cny(subtitles.translationEstimatedCostCny, subtitles.translationEstimateReason)}</span>`;
      el("totalUsageCost").textContent = cny(subtitles.totalEstimatedCostCny, subtitles.totalEstimateReason);
      const latency = subtitles.avgTranslationLatencyMs;
      el("translationLatency").textContent = Number.isFinite(Number(latency)) ? `${(Number(latency) / 1000).toFixed(2)} 秒` : "—";
      updateSubtitleBudget(subtitles, measuredDelay, Number(data.targetDelaySeconds));
      if (data.quality) el("resolution").textContent = `${data.quality.width || "?"}×${data.quality.height || "?"}${data.quality.fps ? ` @ ${data.quality.fps}fps` : ""}`;
    } catch (error) {
      if (!String(error.message).includes("Failed to fetch")) showError(error);
    }
  }

  function updatePlaybackRecovery(data, playerBehind, ahead) {
    if (!video) return;
    if (data?.state !== "running") {
      video.playbackRate = 1;
      sourceWasStalled = false;
      return;
    }
    const publisherStall = Number(data.sourceStallSeconds);
    const health = typeof window.classifySourceHealth === "function"
      ? window.classifySourceHealth({
        state: data.state,
        playlistReady: data.playlistReady,
        sourceStallSeconds: publisherStall,
        sourceIngest: data.sourceIngest,
      })
      : {
        active: Number.isFinite(publisherStall) && publisherStall > 5,
        kind: "upstream",
        stallSeconds: publisherStall,
      };
    // Only an ingest-confirmed outage should pause catch-up. A publisher-only
    // pause is a local packaging hiccup; the player can keep draining its
    // buffer and recover without being forced into a hold state.
    const isStalled = health.active && health.kind === "upstream";
    const stall = isStalled ? Number(health.stallSeconds) : 0;
    const recovered = sourceWasStalled && !isStalled;
    const decision = window.decidePlaybackRecovery?.({
      playerBehind,
      targetDelay: data.targetDelaySeconds,
      hiddenDelay: data.hiddenMediaSeconds,
      bufferAhead: ahead,
      sourceStallSeconds: stall,
      recovered,
    });
    sourceWasStalled = isStalled;
    if (!decision) {
      // Keep a failed/late policy script load from leaving a previous
      // catch-up rate active indefinitely.
      video.playbackRate = 1;
      return;
    }
    // A user pause (and the automatic rebuffer pause) owns playback. Do not
    // leave a catch-up rate armed while the element is not advancing.
    if (video.paused && decision.action !== "hold") {
      video.playbackRate = 1;
      return;
    }
    if (decision.action === "hold") {
      video.playbackRate = 1;
      return;
    }
    if (decision.action === "rate") {
      video.playbackRate = decision.playbackRate;
      return;
    }
    video.playbackRate = 1;
    if (decision.action !== "seek" || video.paused || ahead < 10) return;
    const now = performance.now() / 1000;
    if (now - lastRecoverySeekAt < 10) return;
    const edge = Number.isFinite(Number(latestLevelDetails?.edge))
      ? Number(latestLevelDetails.edge)
      : (video.seekable?.length ? video.seekable.end(video.seekable.length - 1) : null);
    if (!Number.isFinite(edge)) return;
    const start = video.seekable?.length ? video.seekable.start(0) : null;
    const target = edge - decision.desiredDelay;
    if (!Number.isFinite(start) || target < start || target <= video.currentTime + 0.5) return;
    try {
      video.currentTime = target;
    } catch (_) {
      return;
    }
    lastRecoverySeekAt = now;
    // A forward recovery skips media time in one jump. Clear the scheduler's
    // admission cache so the next render is based on the new playhead rather
    // than waiting for the next subtitle poll to discover the jump.
    subtitleScheduler?.retime();
    renderSubtitle();
  }

  let autoPausedForStall = false;

  function updateStallOverlay(data) {
    const banner = el("stallBanner");
    if (!banner) return;
    if (data.state === "error") {
      autoPausedForStall = false;
      setMediaLoading(false);
      banner.hidden = false;
      const detail = String(data.error || "未知错误").slice(0, 80);
      banner.textContent = `直播会话出错：${detail}——请停止后重新启动`;
      // 会话级错误不走 showError，而且后端随后就会把 FFmpeg 的尾部输出发到
      // /api/logs；两条一起看才是完整现场，所以这里也记一条。
      diagnosticsBar?.push("error", "session", detail);
      void flushDevLog();
      return;
    }
    const stall = Number(data.sourceStallSeconds);
    const health = typeof window.classifySourceHealth === "function"
      ? window.classifySourceHealth({
        state: data.state,
        playlistReady: data.playlistReady,
        sourceStallSeconds: stall,
        sourceIngest: data.sourceIngest,
      })
      : {
        active: data.state === "running" && data.playlistReady && Number.isFinite(stall) && stall > 5,
        kind: "upstream",
        stallSeconds: stall,
      };
    if (health.active) {
      banner.hidden = false;
      const message = health.kind === "packaging"
        ? "本地媒体切片处理较慢，正在自动恢复…"
        : "网络端未收到直播流数据，正在尝试重新连接…";
      banner.textContent = `${message}（已停 ${Math.round(health.stallSeconds)} 秒）`;
      if (!video.paused && bufferAhead() < 3) {
        autoPausedForStall = true;
        video.pause();
      }
      return;
    }
    if (autoPausedForStall) {
      if (bufferAhead() >= 10) {
        autoPausedForStall = false;
        banner.hidden = true;
        if (video.paused) video.play().catch(() => {});
      } else {
        banner.hidden = false;
        banner.textContent = "直播流已恢复，正在补充播放缓冲…";
      }
      return;
    }
    banner.hidden = true;
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
    if (!dialog.open) dialog.showModal();
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
      ["deepgram-streaming", "Deepgram Streaming"],
      ["soniox-realtime", "Soniox Realtime STT"],
      ["openai-realtime-transcription", "OpenAI Realtime Transcription"],
      ["assemblyai-streaming", "AssemblyAI Streaming v3"],
      ["volcengine-sauc", "火山引擎豆包大模型流式 ASR (v3 sauc)"],
      ["elevenlabs-scribe-realtime", "ElevenLabs Scribe v2 Realtime"],
      ["speechmatics-realtime", "Speechmatics Realtime v2"],
      ["tencent-asr", "腾讯云实时语音识别"],
    ],
    translation: [
      ["openai-compatible", "OpenAI Compatible"],
      ["anthropic-messages", "Anthropic Messages (Claude)"],
      ["google-genai", "Google Gemini (GenerateContent)"],
    ],
  };

  const providerDefaults = {
    "dashscope-qwen-realtime": { model: "qwen3-asr-flash-realtime", baseUrl: "wss://dashscope.aliyuncs.com/api-ws/v1/realtime", options: { sampleRate: 16000 } },
    "dashscope-task-asr": { model: "fun-asr-realtime-2026-02-28", baseUrl: "wss://dashscope.aliyuncs.com/api-ws/v1/inference", options: { sampleRate: 16000, heartbeat: true } },
    "openai-audio-transcriptions": { model: "", baseUrl: "https://api.openai.com/v1", options: { language: "ja", windowSeconds: 3, requestTimeoutSeconds: 20 } },
    "deepgram-streaming": { model: "nova-3", baseUrl: "wss://api.deepgram.com/v1/listen", options: { interimResults: true, smartFormat: true, endpointingMs: 100, vadEvents: true, utteranceEndMs: 1000, keepAliveSeconds: 8 } },
    "soniox-realtime": { model: "stt-rt-v5", baseUrl: "wss://stt-rt.soniox.com/transcribe-websocket", options: { enableEndpointDetection: true, enableLanguageIdentification: true, enableSpeakerDiarization: true, maxEndpointDelayMs: 700 } },
    "openai-realtime-transcription": { model: "gpt-live-transcribe", baseUrl: "wss://api.openai.com/v1/realtime", options: { delay: "low" } },
    "assemblyai-streaming": { model: "universal-3-5-pro", baseUrl: "wss://streaming.assemblyai.com/v3/ws", options: { mode: "balanced", speakerLabels: true, maxSpeakers: 6 } },
    "volcengine-sauc": { model: "bigmodel_async", baseUrl: "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async", options: { resourceId: "volc.bigasr.sauc.concurrent", authMode: "new" } },
    "elevenlabs-scribe-realtime": { model: "scribe_v2_realtime", baseUrl: "wss://api.elevenlabs.io/v1/speech-to-text/realtime", options: { commitStrategy: "manual", includeLanguageDetection: true } },
    "speechmatics-realtime": { model: "enhanced", baseUrl: "wss://global.rt.speechmatics.com/v2/", options: { enablePartials: true, maxDelaySeconds: 4 } },
    "tencent-asr": { model: "16k_ja", baseUrl: "wss://asr.cloud.tencent.com/asr/v2/<appid>", options: { appId: "", secretId: "", engineModelType: "16k_ja", wordInfo: 0 } },
    "openai-compatible": { model: "", baseUrl: "https://api.openai.com/v1", options: { temperature: 0.3, maxTokens: 256, timeoutSeconds: 6, contextPairs: 6 } },
    "anthropic-messages": { model: "", baseUrl: "https://api.anthropic.com", options: { temperature: 0.3, maxTokens: 256, timeoutSeconds: 6, contextPairs: 6 } },
    "google-genai": { model: "", baseUrl: "https://generativelanguage.googleapis.com/v1beta", options: { temperature: 0.3, maxTokens: 256, timeoutSeconds: 6, contextPairs: 6 } },
  };

  function newProviderId(section) {
    const prefix = section === "asr" ? "asr" : "translation";
    const used = new Set(providerCatalog[section].providers.map((item) => item.id));
    let index = 1;
    while (used.has(`${prefix}-${index}`)) index += 1;
    return `${prefix}-${index}`;
  }

  function applyProviderKindDefaults(provider, kind) {
    const defaults = providerDefaults[kind];
    if (!defaults) return;
    provider.kind = kind;
    provider.model = defaults.model;
    provider.baseUrl = defaults.baseUrl;
    provider.options = structuredClone(defaults.options);
  }

  function addProvider(section) {
    const id = newProviderId(section);
    const kind = section === "asr" ? "soniox-realtime" : "openai-compatible";
    const provider = {
      id,
      label: section === "asr" ? "新语音识别配置" : "新翻译配置",
      kind,
      model: "",
      baseUrl: "",
      apiKey: "",
      options: {},
    };
    applyProviderKindDefaults(provider, kind);
    providerCatalog[section].providers.push(provider);
    editingModel[section] = provider.id;
    renderProviderProfiles();
  }

  function renderProviderProfiles() {
    if (!providerCatalog) return;
    document.querySelector(".asr-section").hidden = editingSection !== "asr";
    document.querySelector(".translation-section").hidden = editingSection !== "translation";
    // 两个页签的高亮以前是写死在 HTML 里的，切过去以后没人搬，所以高亮永远
    // 停在「语音识别配置」上。状态在 editingSection，这里跟着它走。
    el("editAsrConnections")?.classList.toggle("active", editingSection === "asr");
    el("editTranslationConnections")?.classList.toggle("active", editingSection === "translation");
    renderProviderSection("asr", el("asrProfiles"));
    renderProviderSection("translation", el("translationProfiles"));
  }

  function renderProviderSection(section, container) {
    const group = providerCatalog[section];
    container.innerHTML = "";
    const references = section === "translation"
      ? [group.active, providerCatalog.chatTranslation?.active, ...(Array.isArray(group.fallback) ? group.fallback : [])]
      : [group.active];
    const visibleProviders = roleProviders(group, references.concat(editingModel[section]));
    if (!visibleProviders.some(p => p.id === editingModel[section])) editingModel[section] = group.active;
    const list = document.createElement("nav");
    list.className = "connection-list";
    visibleProviders.forEach(p => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "connection-item";
      button.setAttribute("aria-pressed", String(editingModel[section] === p.id));
      const uses = [];
      if (p.id === group.active) uses.push(section === "asr" ? "识别已选" : "字幕已选");
      if (section === "translation" && p.id === providerCatalog.chatTranslation?.active) uses.push("弹幕已选");
      button.textContent = `${p.label || p.model}  ${uses.join(" · ")}`;
      button.addEventListener("click", () => { editingModel[section] = p.id; renderProviderProfiles(); });
      list.append(button);
    });
    container.append(list);
    visibleProviders.filter(p => p.id === editingModel[section]).forEach((provider) => {
      const card = document.createElement("article");
      card.className = "provider-profile";
      card.dataset.providerId = provider.id;
      const isActive = group.active === provider.id;
      const cannotDelete = isActive || (section === "translation" && providerCatalog.chatTranslation?.active === provider.id) || group.providers.length === 1;
      const kinds = providerKinds[section].map(([value, label]) =>
        `<option value="${value}"${provider.kind === value ? " selected" : ""}>${label}</option>`
      ).join("");
      card.innerHTML = `
        <header class="provider-profile-head">
          <strong class="active-provider">正在编辑：${escapeHtml(provider.label || provider.model)}</strong>
          <code>${escapeHtml(provider.id)}</code>
          <button class="secondary compact provider-delete" type="button" data-action="delete" ${cannotDelete ? "disabled" : ""}>删除</button>
        </header>
        <div class="provider-fields">
          <label><span>名称</span><input data-field="label" value="${escapeHtml(provider.label || "")}" required></label>
          <label><span>协议</span><select data-field="kind">${kinds}</select></label>
          <label><span>模型</span><input data-field="model" value="${escapeHtml(provider.model || "")}" placeholder="填写厂商模型 ID" required></label>
          <label><span>Base URL</span><input data-field="baseUrl" value="${escapeHtml(provider.baseUrl || "")}" required></label>
          <label class="wide"><span>API Key</span><input data-field="apiKey" type="password" value="${escapeHtml(provider.apiKey || "")}" autocomplete="off"><small>凭据保存在本机。</small><button type="button" class="secondary compact" data-action="reveal">显示 Key</button></label>
          <details class="wide"><summary>价格与高级设置</summary><div class="provider-fields">${section === "asr" ? asrPricingFields(provider) : translationPricingFields(provider)}
          ${section === "asr" ? asrOptionFields(provider) : translationOptionFields(provider)}</div></details>
        </div>`;
      card.querySelector('[data-action="reveal"]').addEventListener("click", (event) => {
        const input = card.querySelector('[data-field="apiKey"]');
        input.type = input.type === "password" ? "text" : "password";
        event.target.textContent = input.type === "password" ? "显示 Key" : "隐藏 Key";
      });
      card.addEventListener("input", handleProviderInput);
      card.addEventListener("change", handleProviderInput);
      card.querySelector('[data-action="delete"]').addEventListener("click", () => {
        if (cannotDelete) return;
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
      <label><span>缓存写入（CNY / 百万 token）</span><input data-field="pricePerMillionCacheWriteTokensCny" type="number" min="0" step="any" value="${pricingValue(provider.pricePerMillionCacheWriteTokensCny)}" placeholder="可选；无缓存写入可留空"></label>
      <label><span>输出（CNY / 百万 token）</span><input data-field="pricePerMillionOutputTokensCny" type="number" min="0" step="any" value="${pricingValue(provider.pricePerMillionOutputTokensCny)}" placeholder="留空不可估算"></label>`;
  }

  function checked(value) {
    return value ? " checked" : "";
  }

  function asrOptionFields(provider) {
    const options = provider.options || {};
    if (provider.kind === "openai-audio-transcriptions") return `
      <label><span>语言</span><input data-option="language" value="${escapeHtml(options.language || "ja")}"></label>
      <label><span>分窗秒数</span><input data-option="windowSeconds" type="number" min="0.5" step="0.5" value="${Number(options.windowSeconds || 3)}"></label>
      <label><span>请求超时（秒）</span><input data-option="requestTimeoutSeconds" type="number" min="1" step="1" value="${Number(options.requestTimeoutSeconds || 20)}"></label>`;
    if (provider.kind === "soniox-realtime") return `
      <label><span><input data-option="enableEndpointDetection" type="checkbox"${checked(options.enableEndpointDetection !== false)}> 端点检测</span></label>
      <label><span><input data-option="enableLanguageIdentification" type="checkbox"${checked(options.enableLanguageIdentification !== false)}> 语言识别</span></label>
      <label><span><input data-option="enableSpeakerDiarization" type="checkbox"${checked(options.enableSpeakerDiarization)}> 说话人分离</span></label>
      <label><span>最大端点延迟 ms</span><input data-option="maxEndpointDelayMs" type="number" min="500" max="3000" step="100" value="${Number(options.maxEndpointDelayMs || 2000)}"></label>`;
    if (provider.kind === "deepgram-streaming") return `
      <label><span><input data-option="diarize" type="checkbox"${checked(options.diarize)}> 说话人分离（附加计费）</span></label>
      <label><span>Endpointing ms</span><input data-option="endpointingMs" type="number" min="1" step="10" value="${Number(options.endpointingMs || 300)}"></label>
      <label><span>Utterance End ms</span><input data-option="utteranceEndMs" type="number" min="0" step="100" value="${Number(options.utteranceEndMs || 0)}"></label>`;
    if (provider.kind === "assemblyai-streaming") return `
      <label><span>模式</span><select data-option="mode"><option value="balanced"${options.mode === "balanced" ? " selected" : ""}>balanced</option><option value="min_latency"${options.mode === "min_latency" ? " selected" : ""}>min_latency</option><option value="max_accuracy"${options.mode === "max_accuracy" ? " selected" : ""}>max_accuracy</option></select></label>
      <label><span><input data-option="speakerLabels" type="checkbox"${checked(options.speakerLabels)}> 说话人分离</span></label>
      <label><span>最多说话人</span><input data-option="maxSpeakers" type="number" min="1" max="10" step="1" value="${Number(options.maxSpeakers || 6)}"></label>`;
    if (provider.kind === "volcengine-sauc") return `
      <label><span>Resource ID</span><input data-option="resourceId" value="${escapeHtml(options.resourceId || "volc.bigasr.sauc.concurrent")}"></label>
      <label><span>旧控制台 App Key</span><input data-option="appKey" value="${escapeHtml(options.appKey || "")}"></label>
      <label><span>鉴权模式</span><select data-option="authMode"><option value="new"${options.authMode !== "legacy" ? " selected" : ""}>新控制台</option><option value="legacy"${options.authMode === "legacy" ? " selected" : ""}>旧控制台</option></select></label>`;
    if (provider.kind === "elevenlabs-scribe-realtime") return `
      <label><span>提交策略</span><select data-option="commitStrategy"><option value="manual"${options.commitStrategy !== "vad" ? " selected" : ""}>manual</option><option value="vad"${options.commitStrategy === "vad" ? " selected" : ""}>vad</option></select></label>
      <label><span><input data-option="includeTimestamps" type="checkbox"${checked(options.includeTimestamps)}> 延迟词时间戳</span></label>
      <label><span><input data-option="includeLanguageDetection" type="checkbox"${checked(options.includeLanguageDetection)}> 返回检测语言</span></label>`;
    if (provider.kind === "speechmatics-realtime") return `
      <label><span><input data-option="enablePartials" type="checkbox"${checked(options.enablePartials !== false)}> 中间结果</span></label>
      <label><span><input data-option="diarization" type="checkbox"${checked(options.diarization)}> 说话人分离</span></label>
      <label><span>最多说话人</span><input data-option="maxSpeakers" type="number" min="2" step="1" value="${Number(options.maxSpeakers || 6)}"></label>
      <label><span>最大延迟秒数</span><input data-option="maxDelaySeconds" type="number" min="0.7" max="4" step="0.1" value="${Number(options.maxDelaySeconds || 4)}"></label>`;
    if (provider.kind === "tencent-asr") return `
      <label><span>App ID</span><input data-option="appId" value="${escapeHtml(options.appId || "")}" required></label>
      <label><span>Secret ID</span><input data-option="secretId" value="${escapeHtml(options.secretId || "")}" required></label>
      <label><span>引擎</span><input data-option="engineModelType" value="${escapeHtml(options.engineModelType || provider.model || "16k_ja")}"></label>
      <label><span>Word Info</span><input data-option="wordInfo" type="number" min="0" max="2" step="1" value="${Number(options.wordInfo || 0)}"></label>`;
    if (provider.kind === "dashscope-task-asr") return `
      <label><span>Vocabulary ID</span><input data-option="vocabularyId" value="${escapeHtml(options.vocabularyId || "")}"></label>
      <label><span><input data-option="heartbeat" type="checkbox"${checked(options.heartbeat)}> 静音保活</span></label>`;
    return "";
  }

  function translationOptionFields(provider) {
    const options = provider.options || {};
    return `
      <label><span>温度</span><input data-option="temperature" type="number" min="0" max="2" step="0.1" value="${Number(options.temperature ?? 0.3)}"></label>
      <label><span>最大 Tokens</span><input data-option="maxTokens" type="number" min="32" step="1" value="${Number(options.maxTokens || 256)}"></label>
      <label><span>超时（秒）</span><input data-option="timeoutSeconds" type="number" min="1" step="1" value="${Number(options.timeoutSeconds || 6)}"></label>
      <label><span>字幕上下文对数（弹幕不使用）</span><input data-option="contextPairs" type="number" min="0" step="1" value="${Number(options.contextPairs ?? 6)}"></label>`;
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
      if (field === "apiKey") provider.apiKeyConfigured = Boolean(String(event.target.value || "").trim());
    }
    if (option) {
      provider.options ||= {};
      provider.options[option] = event.target.type === "number"
        ? Number(event.target.value)
        : event.target.type === "checkbox"
          ? event.target.checked
          : event.target.value;
    }
    if (field === "kind") {
      // 模型、地址和选项描述的是"怎么连"，必须跟着协议一起换。之前这里保留
      // 旧值并让用户自己核对，结果就是协议写 Soniox、地址是 DashScope、模型是
      // Fun-ASR 这样连不上的组合，而且用户没有任何依据知道该填什么。
      // 名称和 API Key 不动：前者是用户起的名字，后者是凭据，清掉不可恢复。
      applyProviderKindDefaults(provider, provider.kind);
      renderProviderProfiles();
      el("modelSettingsFeedback").textContent = "已应用该协议的默认模型与地址（名称和 API Key 保持不变）。";
    }
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  }

  function closeModelSettings() {
    el("modelSettingsDialog").close();
  }

  function updateCookiePlatformHelp() {
    const platform = el("cookiePlatform").value;
    const bilibili = platform === "bilibili";
    const twitch = platform === "twitch";
    el("cookieImportIntro").textContent = bilibili
      ? "从 bilibili.com 的 DevTools Cookie 列表复制名称/值，或粘贴 Cookie 请求头 / Netscape 文件。yt-dlp 登录检查只要求 SESSDATA；其他有效 Cookie 会一并保留。"
      : twitch
        ? "从 twitch.tv 的 DevTools Cookie 列表复制名称/值，或粘贴 Cookie 请求头 / Netscape 文件。公开 Twitch 直播不要求 Cookie。"
        : "从 youtube.com（必要时包括 Google 登录域）的 DevTools Cookie 列表复制名称/值，或粘贴 Cookie 请求头 / Netscape 文件。";
    el("cookiePayload").placeholder = bilibili
      ? "SESSDATA　xxxxxxxx…\nbili_jct　yyyyyyyy…\nDedeUserID　12345"
      : twitch
        ? "auth-token　xxxxxxxx…\npersistent　1\n（公开直播可不导入）"
        : "SID　xxxxxxxx…\nHSID　yyyyyyyy…\n（直接从 DevTools Cookie 列表复制即可）";
  }

  function openCookieImport() {
    const feedback = el("cookieImportFeedback");
    feedback.textContent = "";
    feedback.dataset.tone = "";
    updateCookiePlatformHelp();
    const dialog = el("cookieImportDialog");
    if (!dialog.open) dialog.showModal();
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
        const platformName = data.platform === "bilibili" ? "Bilibili" : data.platform === "twitch" ? "Twitch" : "YouTube";
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
      renderRoleSelectors(data);
      renderProviderProfiles();
      feedback.textContent = "已保存。下次启动直播时使用新配置。";
      feedback.dataset.tone = "success";
      if (targetSelector && data.subtitle?.targetLanguage) targetSelector.value = data.subtitle.targetLanguage;
      await refreshLanguageCapabilities();
    } catch (error) {
      feedback.textContent = error.message || String(error);
      feedback.dataset.tone = "error";
    } finally {
      save.disabled = false;
    }
  }

  function sourcePolicyFromUi() {
    if (el("sourceLanguageMode").value === "detect") {
      const candidates = languageState.candidates.slice();
      const policy = {
        mode: "detect",
        candidates,
        allowCodeSwitching: el("allowCodeSwitching").checked,
      };
      // The first candidate doubles as the preferred language.
      if (candidates.length) policy.preferred = candidates[0];
      return policy;
    }
    return { mode: "specified", tag: sourceSelector?.value || "ja" };
  }

  function applySourcePolicyToUi(policy) {
    if (policy?.mode === "detect") {
      el("sourceLanguageMode").value = "detect";
      languageState.candidates = Array.isArray(policy.candidates) ? policy.candidates.slice() : [];
      el("allowCodeSwitching").checked = !!policy.allowCodeSwitching;
    } else {
      el("sourceLanguageMode").value = "specified";
      languageState.candidates = [];
      el("allowCodeSwitching").checked = false;
      if (sourceSelector && policy?.tag) sourceSelector.value = policy.tag;
    }
    renderCandidateChips();
    updateSourceModeVisibility();
  }

  function updateSourceModeVisibility() {
    const detect = el("sourceLanguageMode").value === "detect";
    el("sourceSpecifiedField").hidden = detect;
    el("sourceDetectField").hidden = !detect;
  }

  function addCandidate(tag) {
    if (!tag || languageState.candidates.includes(tag)) return;
    languageState.candidates.push(tag);
    renderCandidateChips();
    persistLanguageSettings();
  }

  function removeCandidate(tag) {
    languageState.candidates = languageState.candidates.filter((item) => item !== tag);
    renderCandidateChips();
    persistLanguageSettings();
  }

  function renderCandidateChips() {
    const container = el("sourceCandidateChips");
    container.innerHTML = "";
    languageState.candidates.forEach((tag, index) => {
      const parts = window.LingerLensLanguages.nameParts(tag);
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "language-chip";
      chip.dir = "auto";
      chip.textContent = `${index === 0 ? "首选 " : ""}${parts.autonym} · ${tag} ×`;
      chip.setAttribute("aria-label", `移除候选语言 ${parts.primary}`);
      chip.addEventListener("click", () => removeCandidate(tag));
      container.append(chip);
    });
  }

  function refreshLanguageCapabilityUi() {
    const asr = languageState.asr?.language;
    const detectOption = el("sourceLanguageMode").querySelector('option[value="detect"]');
    const detectUnsupported = !!asr && asr.detection === "none";
    detectOption.disabled = detectUnsupported;
    if (detectUnsupported && el("sourceLanguageMode").value === "detect") el("sourceLanguageMode").value = "specified";
    updateSourceModeVisibility();
  }

  function tagCoveredBy(tag, supportedTags) {
    if (supportedTags == null) return true;
    if (supportedTags.includes(tag)) return true;
    // BCP 47 basic-range match: an unqualified supported tag (zh) covers
    // more specific policy tags (zh-Hans).
    return supportedTags.includes(String(tag).split("-")[0]);
  }

  function validateLanguageSettingsClient() {
    // The server stays the final authority; this only shortens the feedback
    // loop with the effective capabilities the loopback API reported.
    const asr = languageState.asr?.language;
    const policy = sourcePolicyFromUi();
    if (asr) {
      if (policy.mode === "specified") {
        if (!tagCoveredBy(policy.tag, asr.supportedTags)) return `当前 ASR 不支持源语言 ${policy.tag}`;
      } else {
        if (asr.detection === "none") return "当前 ASR 不支持自动识别源语言，请指定语言";
        if (asr.detection === "candidates" && !policy.candidates.length) return "当前 ASR 自动识别需要候选语言";
        if (asr.maxCandidates != null && policy.candidates.length > asr.maxCandidates) return `当前 ASR 自动识别最多支持 ${asr.maxCandidates} 个候选语言`;
        const candidateScope = asr.detectionTags ?? asr.supportedTags;
        const unsupported = policy.candidates.filter((tag) => !tagCoveredBy(tag, candidateScope));
        if (unsupported.length) return `当前 ASR 不支持候选语言：${unsupported.join("、")}`;
        if (policy.allowCodeSwitching && !asr.codeSwitching) return "当前 ASR 不支持混合语言（code-switching）识别";
      }
    }
    const translation = languageState.translation?.language;
    const target = targetSelector?.value;
    if (translation && target && !translation.openWorldPrompting && !tagCoveredBy(target, translation.targetTags)) {
      return `当前翻译 Provider 不支持目标语言 ${target}`;
    }
    return null;
  }

  async function persistLanguageSettings() {
    if (!languageState.subtitle) return;
    const problem = validateLanguageSettingsClient();
    if (problem) {
      showError(new Error(problem));
      return;
    }
    const previousTarget = languageState.subtitle.targetLanguage;
    const patch = {
      ...languageState.subtitle,
      sourceLanguage: sourcePolicyFromUi(),
      targetLanguage: targetSelector?.value || "zh-Hans",
    };
    try {
      const data = await request("/api/providers", { subtitle: patch });
      languageState.subtitle = data.subtitle;
      if (patch.targetLanguage !== previousTarget) {
        const parts = window.LingerLensLanguages.nameParts(patch.targetLanguage);
        el("message").textContent = `目标语言已切换为 ${parts.primary}；后续字幕立即使用新语言。`;
      }
    } catch (error) {
      showError(error);
    }
  }

  function initLanguageSelectors(defaults) {
    if (!targetSelector) {
      targetSelector = window.LingerLensLanguages.createLanguageSelector(el("targetLanguage"), {
        onChange: () => {
          // Direction and any currently visible rows update synchronously;
          // persistLanguageSettings hot-switches subsequent translations.
          renderSubtitle();
          persistLanguageSettings();
        },
      });
      sourceSelector = window.LingerLensLanguages.createLanguageSelector(el("sourceLanguage"), {
        onChange: () => persistLanguageSettings(),
      });
      candidateSelector = window.LingerLensLanguages.createLanguageSelector(el("sourceCandidates"), {
        placeholder: "添加候选语言…",
        onChange: (tag) => { addCandidate(tag); candidateSelector.value = ""; },
      });
    }
    applySourcePolicyToUi(defaults.sourceLanguage || { mode: "specified", tag: "ja" });
    targetSelector.value = defaults.targetLanguage || "zh-Hans";
  }

  async function refreshLanguageCapabilities() {
    const languages = await request("/api/languages");
    window.LingerLensLanguages.setCatalog(languages.languages);
    languageState.asr = languages.asr;
    languageState.translation = languages.translation;
    refreshLanguageCapabilityUi();
  }

  async function loadLanguageSettings() {
    try {
      const [languages, providers] = await Promise.all([request("/api/languages"), request("/api/providers")]);
      window.LingerLensLanguages.setCatalog(languages.languages);
      languageState.asr = languages.asr;
      languageState.translation = languages.translation;
      renderRoleSelectors(providers);
      languageState.subtitle = providers.subtitle || {};
      initLanguageSelectors(languages.defaults || {});
      refreshLanguageCapabilityUi();
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
    // Only two timing provenances exist: word-level ("asr") and VAD/frontier
    // ("vad"). A third "approx" state used to be shown here and was always 0,
    // because CaptionChunk.begin_pcm is a non-optional float.
    el("timingSources").textContent = total
      ? `asr ${Math.round((100 * Number(counts.asr || 0)) / total)}% · vad ${Math.round((100 * Number(counts.vad || 0)) / total)}%`
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
      localStorage.setItem("lingerlens.targetDelaySeconds", String(suggested));
      el("message").textContent = `已把本地延迟调到 ${suggested} 秒。字幕就绪预算现在有余量；负载回落后可手动调回。`;
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
      // Keep ten minutes client-side so a typical live session exposes at
      // least about 100 recent cues. The DOM remains separately capped below.
      const cutoff = newest - 605;
      for (const [id, cue] of subtitleCues) {
        if (cue.tEnd + cue.hold < cutoff) subtitleCues.delete(id);
      }
    } catch (error) {
      if (!String(error.message).includes("Failed to fetch")) console.warn("subtitle poll failed", error);
    }
  }

  function playingWallClock() {
    if (Number.isFinite(window.__lingerlensSubtitleTestWallTime)) return window.__lingerlensSubtitleTestWallTime;
    return mediaClock?.playingWallTime() ?? null;
  }

  function renderSubtitlesTimeline(wallClock) {
    const container = el("subtitlesTimelineList");
    if (!container) return;
    if (!Number.isFinite(wallClock)) {
      if (container.children.length === 0) {
        container.innerHTML = '<div class="timeline-empty-state">等待字幕生成...</div>';
      }
      return;
    }

    const t = wallClock + subtitlePrefs.offset;
    const sortedCues = [...subtitleCues.values()]
      .filter((cue) => cue && (cue.state === "done" && typeof cue.zh === "string" && cue.zh.trim()) && cue.tStart <= t)
      .sort((a, b) => a.tStart - b.tStart);

    const empty = container.querySelector(".timeline-empty-state");
    if (sortedCues.length === 0) {
      if (!empty) container.innerHTML = '<div class="timeline-empty-state">等待字幕生成...</div>';
      return;
    }
    if (empty) empty.remove();

    const existingRows = new Map();
    for (const child of container.children) {
      if (child.dataset?.cueId) existingRows.set(child.dataset.cueId, child);
    }

    const activeCue = subtitleScheduler ? subtitleScheduler.pick([...subtitleCues.values()], t) : null;
    const maxEntries = 100;
    const sliced = sortedCues.slice(-maxEntries);
    // dataset values are always strings. Comparing them with numeric cue IDs
    // made every retained row look obsolete, rebuilding all 100 rows at 4 Hz.
    const visibleIds = new Set(sliced.map((cue) => String(cue.id)));

    // Remove obsolete
    for (const [id, elem] of existingRows) {
      if (!visibleIds.has(id)) elem.remove();
    }

    const fmtTime = window.formatWallClockTime || ((s) => `${Math.floor(s)}s`);
    const timingFor = window.subtitleTimelineTiming || ((start, offset, format) => {
      const wallTime = start - offset;
      return { wallTime, label: format(wallTime) };
    });
    let hasNewAppended = false;

    for (const cue of sliced) {
      let row = existingRows.get(String(cue.id));
      const isActive = activeCue && activeCue.id === cue.id;
      const timing = timingFor(cue.tStart, subtitlePrefs.offset, fmtTime);
      if (!row) {
        row = document.createElement("div");
        row.className = "timeline-row subtitle-timeline-row";
        row.dataset.cueId = cue.id;
        row.innerHTML = `
          <div class="timeline-time"></div>
          <div class="timeline-body">
            ${cue.zh ? `<div class="timeline-text-translated">${escapeHtml(cue.zh)}</div>` : ""}
            ${cue.src ? `<div class="timeline-text-source">${escapeHtml(cue.src)}</div>` : ""}
          </div>
        `;
        row.tabIndex = 0;
        const seekCue = () => seekToWallTime(Number(row.dataset.seekWallTime), row);
        row.addEventListener("click", seekCue);
        row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); seekCue(); } });
        container.appendChild(row);
        hasNewAppended = true;
      }
      const seekWallTime = String(timing.wallTime);
      if (row.dataset.seekWallTime !== seekWallTime) row.dataset.seekWallTime = seekWallTime;
      const timeElement = row.querySelector(".timeline-time");
      if (timeElement.textContent !== timing.label) timeElement.textContent = timing.label;
      if (row.classList.contains("is-current") !== Boolean(isActive)) {
        row.classList.toggle("is-current", Boolean(isActive));
      }
    }

    if (hasNewAppended && followSubtitlesController) {
      followSubtitlesController.onNewContent();
    }
  }

  function seekToWallTime(wallTime, row) {
    const position = mediaClock?.mediaPositionForWallTime(wallTime);
    if (!Number.isFinite(position) || !video.seekable.length) return;
    const start = video.seekable.start(0);
    const end = video.seekable.end(video.seekable.length - 1);
    if (position < start || position > end) {
      row?.setAttribute("aria-disabled", "true");
      row?.setAttribute("title", "已超出本地回看窗口");
      return;
    }
    video.currentTime = position;
  }

  function updatePlayerControls() {
    const range = mediaClock?.seekableWallClockRange();
    const rail = el("seekRail");
    const playLabel = video.paused ? "播放" : "暂停";
    const muteLabel = video.muted ? "取消静音" : "静音";
    el("playPause").setAttribute("aria-label", playLabel);
    el("playPause").setAttribute("title", playLabel);
    el("muteToggle").setAttribute("aria-label", muteLabel);
    el("muteToggle").setAttribute("title", muteLabel);
    stage.classList.toggle("is-paused", video.paused);
    stage.classList.toggle("is-muted", video.muted);
    el("currentWallTime").textContent = mediaClock?.formatTime(range?.currentWallTime) || "--:--:--";
    if (!range || !rail) return;
    rail.min = String(range.startPosition);
    rail.max = String(range.endPosition);
    if (!rail.matches(":active")) rail.value = String(Math.min(range.endPosition, Math.max(range.startPosition, video.currentTime)));
    el("seekStartWallTime").textContent = mediaClock.formatTime(range.startWallTime);
    el("seekEndWallTime").textContent = mediaClock.formatTime(range.endWallTime);
    if (!rail.dataset.dragging) el("seekPreview").textContent = mediaClock.formatTime(range.currentWallTime);
  }

  let playerControlsHideTimer = null;

  function setPlayerControlsVisible(visible) {
    stage.classList.toggle("controls-visible", visible);
  }

  function schedulePlayerControlsHide() {
    clearTimeout(playerControlsHideTimer);
    setPlayerControlsVisible(true);
    if (video.paused) return;
    playerControlsHideTimer = setTimeout(() => setPlayerControlsVisible(false), 1600);
  }

  function revealPlayerControls() {
    clearTimeout(playerControlsHideTimer);
    setPlayerControlsVisible(true);
  }

  function updateFullscreenControl() {
    const fullscreen = document.fullscreenElement === stage;
    const label = fullscreen ? "退出全屏" : "进入全屏";
    stage.classList.toggle("is-fullscreen", fullscreen);
    el("toggleFullscreen").setAttribute("aria-label", label);
    el("toggleFullscreen").setAttribute("title", label);
  }

  function updateLiveMessagesUI(data) {
    // Stats update
    const stats = data.stats || {};
    const indicator = el("chatStatsIndicator");
    if (indicator) {
      // 这个芯片是面板头里唯一长度无上限的内容，而面板头固定 52px。把状态词
      // 一路用 " · " 拼下去实测能到 515px，足以把标题挤没、把徽章盖住。
      // 所以分两桶：异常优先，常态次之，最多显示两条；被折叠掉的项仍完整
      // 保留在 title 里，悬停即可看到。
      const problems = [];
      const routine = [];
      const receiveState = {
        idle: "未监听", connecting: "正在连接聊天", authenticating: "正在验证聊天连接", reconnecting: "正在重连聊天",
        running: "聊天接收中", polling: "轮询接收中", error: "聊天接收失败", unavailable: "聊天不可用",
      }[stats.state];
      const receiveIsProblem = stats.state === "reconnecting" || stats.state === "error" || stats.state === "unavailable";
      if (receiveState) (receiveIsProblem ? problems : routine).push(receiveState);
      if (data.error || stats.lastError) problems.push(data.error || stats.lastError);
      if (stats.pendingClock) problems.push(`等待媒体时钟: ${stats.pendingClock}`);
      if (stats.received !== undefined) routine.push(`收到: ${stats.received}`);
      if (stats.translated !== undefined) routine.push(`翻译: ${stats.translated}`);
      if (stats.translationFailed) problems.push(`失败: ${stats.translationFailed}`);
      const failureLabels = { timeout: "翻译超时", deadline: "翻译预算耗尽", empty: "空译文", json_format: "JSON格式错误", batch_count: "批次数量不符", batch_item: "批次内容或编号异常", batch_ids: "批次编号不符", response_format: "响应格式异常", rate_limit: "翻译限流", authentication: "翻译认证失败", provider_error: "翻译调用失败" };
      if (stats.translationLastFailure) problems.push(failureLabels[stats.translationLastFailure] || "翻译失败");
      if (chatOverlay.getStats().dropped) routine.push(`画面省略: ${chatOverlay.getStats().dropped}`);
      if (stats.translationSkipped) routine.push(`跳过: ${stats.translationSkipped}`);
      const everything = [...problems, ...routine];
      const shown = (problems.length > 0 ? problems : routine).slice(0, 2);
      indicator.textContent = shown.length > 0 ? shown.join(" · ") : "聊天室就绪";
      indicator.title = everything.length > shown.length ? everything.join(" · ") : indicator.textContent;
    }

    // Toggle sync
    const toggle = el("chatTranslateToggle");
    if (toggle && data.translate !== undefined && document.activeElement !== toggle) {
      toggle.checked = Boolean(data.translate);
    }
    const target = el("chatTranslationTarget");
    if (target) {
      const state = stats.translationState === "degraded" ? " · 降级" : "";
      target.textContent = `目标语言 ${stats.targetLanguage || targetSelector?.value || "—"}${state}`;
    }
  }

  function renderTimelines() {
    const wall = playingWallClock();
    renderSubtitlesTimeline(wall);
    chatOverlay.render(wall, liveMessagesClient ? [...liveMessagesClient.getStore().values()] : [], {
      enabled: chatOverlayToggle.checked, translated: el("chatTranslateToggle").checked,
      size: Number(chatOverlaySize.value),
      paused: video.paused, filterPureEmoji: el("hidePureEmojiToggle").checked,
    });
    if (liveMessagesTimeline) {
      const result = liveMessagesTimeline.render(wall);
      if (result.changed && followChatController) {
        followChatController.onNewContent();
      }
    }
  }

  const anonymousSpeakerKey = "__anonymous__";

  function speakerKey(cue) {
    const value = cue?.speaker;
    return value === null || value === undefined || String(value).trim() === ""
      ? anonymousSpeakerKey
      : String(value);
  }

  function speakerColorIndex(cue) {
    const key = speakerKey(cue);
    if (key === anonymousSpeakerKey) return -1;
    // Stable and bounded: no per-speaker registry grows during long sessions.
    // Ten visual slots are intentionally reused when diarization reports >10 labels.
    let hash = 0;
    for (const character of key) hash = ((hash * 31) + character.codePointAt(0)) >>> 0;
    return hash % 10;
  }

  function renderSubtitle() {
    const layer = el("subtitleLayer");
    if (!subtitlePrefs.enabled || !subtitleScheduler) { clearSubtitle(); return; }
    const wall = playingWallClock();
    if (!Number.isFinite(wall)) { clearSubtitle(); return; }
    const t = wall + subtitlePrefs.offset + SUBTITLE_RENDER_ADVANCE_SECONDS;
    // Every cue owns its own display window. active() returns all ready cues
    // whose windows contain the playhead, so overlapping speakers/utterances
    // remain visible as independent rows rather than replacing one another.
    const cueValues = [...subtitleCues.values()];
    const cues = typeof subtitleScheduler.active === "function"
      ? subtitleScheduler.active(cueValues, t)
      : [subtitleScheduler.pick(cueValues, t)].filter(Boolean);
    if (!cues.length) { clearSubtitle(); return; }
    if (layer.classList.contains("off")) layer.classList.remove("off");
    const content = layer.querySelector(".subtitle-content");
    const rowsById = new Map([...content.children].map((child) => [child.dataset.cueId, child]));
    const visibleIds = new Set(cues.map((cue) => String(cue.id)));
    for (const [id, child] of rowsById) {
      if (!visibleIds.has(id)) {
        child.remove();
        rowsById.delete(id);
      }
    }
    const targetLanguage = targetSelector?.value || "";
    for (let index = 0; index < cues.length; index += 1) {
      const cue = cues[index];
      const id = String(cue.id);
      let row = rowsById.get(id);
      if (!row) {
        row = document.createElement("div");
        row.className = "subtitle-cue-row";
        row.dataset.cueId = id;
        // 译文在上、原文在下：读的是译文，原文只是参考。反过来的话视线每读
        // 一行都要先跳过原文。
        row.innerHTML = '<div class="subtitle-zh"></div><div class="subtitle-src"></div>';
        rowsById.set(id, row);
      }
      // Cue updates advance seq/revision. Include visible fields as a defensive
      // fallback for test seams and provider metadata revisions.
      const renderRevision = [
        cue.seq, cue.revision, cue.speaker, cue.src, cue.zh, cue.lang, targetLanguage,
      ].join("\u001f");
      if (row.dataset.renderRevision !== renderRevision) {
        const colorIndex = speakerColorIndex(cue);
        if (colorIndex >= 0) row.dataset.speakerColor = String(colorIndex);
        else delete row.dataset.speakerColor;
        row.dataset.speaker = speakerKey(cue);
        row.setAttribute("aria-label", cue.speaker ? `说话人 ${cue.speaker}` : "字幕");
        const zhLine = row.querySelector(".subtitle-zh");
        const srcLine = row.querySelector(".subtitle-src");
        zhLine.textContent = cue.zh || "";
        srcLine.textContent = cue.src || "";
        // Bidi: each line resolves its own direction (RTL translations never leak
        // into the LTR source line). dir=auto is primary; the catalog direction
        // is kept on data-direction as the fallback for old browsers.
        zhLine.dir = "auto";
        srcLine.dir = "auto";
        srcLine.dataset.direction = window.LingerLensLanguages?.directionFor(cue.lang) || "ltr";
        zhLine.dataset.direction = window.LingerLensLanguages?.directionFor(targetLanguage) || "ltr";
        row.dataset.renderRevision = renderRevision;
      }
      // Reorder only when the active cue order really changed. Re-appending all
      // rows every 100ms forced layout and repaint over the video surface.
      if (content.children[index] !== row) {
        content.insertBefore(row, content.children[index] || null);
      }
    }
  }

  function clearSubtitle() {
    const layer = el("subtitleLayer");
    if (!layer.classList.contains("off")) layer.classList.add("off");
    const content = layer.querySelector(".subtitle-content");
    if (content.childElementCount) content.replaceChildren();
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
      el("subtitleSourceColor").value = windowPrefs.sourceColor;
      el("subtitleTranslationColor").value = windowPrefs.translationColor;
    }
  }

  function updateSubtitleOffset() {
    const offset = Number(el("subtitleOffset").value);
    if (!Number.isFinite(offset)) return;
    subtitlePrefs.offset = offset;
    localStorage.setItem("lingerlens.subtitle.offset", String(offset));
    applySubtitlePrefs();
    // Admissions are derived from the effective playhead. Re-evaluate them
    // when the user moves that playhead, then paint synchronously so the
    // control does not wait for a render, timeline, or subtitle-polling tick.
    subtitleScheduler?.retime();
    renderSubtitle();
    renderTimelines();
  }

  function updateSubtitleWindowStyle() {
    subtitleWindow?.updateStyle({
      opacity: Number(el("subtitleOpacity").value),
      sourceColor: el("subtitleSourceColor").value,
      translationColor: el("subtitleTranslationColor").value,
    });
  }

  function beginSubtitleDrag(event) {
    if (!subtitleWindow || event.button !== 0 || event.target.closest(".subtitle-resize-handle")) return;
    event.preventDefault();
    const layer = el("subtitleLayer");
    const stageRect = stage.getBoundingClientRect();
    const start = subtitleWindow.preferences();
    const startX = event.clientX;
    const startY = event.clientY;
    layer.classList.add("is-dragging");
    const move = (pointer) => subtitleWindow.moveTo(
      start.x + (pointer.clientX - startX) / stageRect.width,
      start.y + (pointer.clientY - startY) / stageRect.height,
    );
    const end = () => {
      layer.classList.remove("is-dragging");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end, { once: true });
    window.addEventListener("pointercancel", end, { once: true });
  }

  function beginSubtitleResize(event) {
    if (!subtitleWindow || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const layer = el("subtitleLayer");
    const start = subtitleWindow.preferences();
    const startX = event.clientX;
    const startY = event.clientY;
    const diagonal = Math.max(80, Math.hypot(layer.offsetWidth, layer.offsetHeight));
    layer.classList.add("is-resizing");
    const resize = (pointer) => {
      const delta = ((pointer.clientX - startX) + (pointer.clientY - startY)) / diagonal;
      subtitleWindow.updateStyle({ scale: start.scale + delta });
    };
    const end = () => {
      layer.classList.remove("is-resizing");
      window.removeEventListener("pointermove", resize);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
    };
    window.addEventListener("pointermove", resize);
    window.addEventListener("pointerup", end, { once: true });
    window.addEventListener("pointercancel", end, { once: true });
  }

  function showError(error) {
    const message = error.message || String(error);
    // 所有用户可见的报错都在这里汇合，诊断栏因此只需要这一个钩子。
    diagnosticsBar?.push("error", "ui", message);
    // 最接近崩溃的一类现场优先落盘，不等轮询那一秒。
    void flushDevLog();
    setMediaLoading(false);
    setState("错误", "error");
    el("message").textContent = message;
    el("setupFeedback").textContent = message;
  }

  function setMediaLoading(visible, text) {
    const loading = el("mediaLoading");
    loading.hidden = !visible;
    stage.classList.toggle("is-loading", visible);
    if (text) el("mediaLoadingText").textContent = text;
  }

  function setSessionAction(action) {
    sessionAction = action;
    const starting = action === "starting";
    const stopping = action === "stopping";
    if (starting) el("start").setAttribute("aria-busy", "true");
    else el("start").removeAttribute("aria-busy");
    if (stopping) el("stop").setAttribute("aria-busy", "true");
    else el("stop").removeAttribute("aria-busy");
    el("start").querySelector(".button-label").textContent = starting ? "正在启动" : "启动";
    el("stop").querySelector(".button-label").textContent = stopping ? "正在停止" : "停止";
    renderSessionControls();
  }

  function renderSessionControls() {
    const sessionCanStop = lastSessionState === "running" || lastSessionState === "error";
    stage.classList.toggle("session-active", sessionCanStop || sessionAction !== null);
    el("probe").disabled = controlsBusy;
    el("start").disabled = controlsBusy || sessionAction !== null || el("quality").disabled;
    // An errored source can still own FFmpeg, subtitle, chat and auth resources.
    // Keep Stop available until the idempotent /api/stop cleanup has completed.
    el("stop").disabled = sessionAction !== null || !sessionCanStop;
    el("playPause").disabled = sessionAction === "stopping";
    el("url").disabled = controlsBusy;
    el("targetDelay").disabled = controlsBusy;
  }

  function setBusy(busy) {
    controlsBusy = busy;
    renderSessionControls();
  }

  el("probe").addEventListener("click", probe);
  el("url").addEventListener("input", () => {
    el("quality").disabled = true;
    el("setupPlayback").hidden = true;
    el("setupFeedback").textContent = "点击准备，读取这场直播的清晰度。";
    renderSessionControls();
  });
  el("start").addEventListener("click", start);
  el("stop").addEventListener("click", stop);
  el("applyDelayButton").addEventListener("click", applySuggestedDelay);
  el("toggleFullscreen").addEventListener("click", () => subtitleWindow?.toggleFullscreen());
  el("playPause").addEventListener("click", async () => {
    if (sessionAction === "stopping") return;
    if (!video.paused) {
      video.pause();
      updatePlayerControls();
      return;
    }
    setMediaLoading(true, "正在恢复播放…");
    try {
      await video.play();
    } catch (error) {
      setMediaLoading(false);
      setState("播放失败", "error");
      el("message").textContent = `无法开始播放：${error.message || error}`;
    }
  });
  el("muteToggle").addEventListener("click", () => { video.muted = !video.muted; if (!video.muted && video.volume === 0) video.volume = 0.8; el("volume").value = String(video.muted ? 0 : video.volume); });
  el("volume").addEventListener("input", () => { video.volume = Number(el("volume").value); video.muted = video.volume === 0; revealPlayerControls(); });
  el("seekRail").addEventListener("input", () => { el("seekRail").dataset.dragging = "true"; el("seekPreview").textContent = mediaClock?.formatTime(mediaClock.wallTimeForMediaPosition(Number(el("seekRail").value))) || "--:--:--"; revealPlayerControls(); });
  el("seekRail").addEventListener("change", () => { video.currentTime = Number(el("seekRail").value); delete el("seekRail").dataset.dragging; schedulePlayerControlsHide(); });
  stage.addEventListener("pointermove", schedulePlayerControlsHide);
  stage.addEventListener("pointerenter", revealPlayerControls);
  stage.addEventListener("pointerleave", schedulePlayerControlsHide);
  stage.addEventListener("focusin", revealPlayerControls);
  stage.addEventListener("focusout", schedulePlayerControlsHide);
  el("subtitleLayer").addEventListener("pointerdown", beginSubtitleDrag);
  el("subtitleLayer").addEventListener("keydown", (event) => {
    if (subtitleWindow?.nudge(event.key, event.shiftKey)) event.preventDefault();
  });
  el("subtitleResizeHandle").addEventListener("pointerdown", beginSubtitleResize);
  el("resetSubtitlePosition").addEventListener("click", () => { subtitleWindow?.reset(); applySubtitlePrefs(); });
  ["subtitleOpacity", "subtitleSourceColor", "subtitleTranslationColor"].forEach((id) => el(id).addEventListener("input", updateSubtitleWindowStyle));
  document.addEventListener("fullscreenchange", () => { subtitleWindow?.apply(); updateFullscreenControl(); schedulePlayerControlsHide(); });
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
  el("manageModelConnections").addEventListener("click", openModelSettings);
  el("editAsrConnections").addEventListener("click", () => { editingSection = "asr"; renderProviderProfiles(); });
  el("editTranslationConnections").addEventListener("click", () => { editingSection = "translation"; renderProviderProfiles(); });
  el("roleAsr").addEventListener("change", () => selectRole("roleAsr", "asr"));
  el("roleSubtitle").addEventListener("change", () => selectRole("roleSubtitle", "translation"));
  el("roleFallback").addEventListener("change", () => selectRole("roleFallback", "translationFallback"));
  el("roleChat").addEventListener("change", () => selectRole("roleChat", "chatTranslation"));
  el("modelSettingsForm").addEventListener("submit", saveModelSettings);
  el("sourceLanguageMode").addEventListener("change", () => { updateSourceModeVisibility(); persistLanguageSettings(); });
  el("allowCodeSwitching").addEventListener("change", () => persistLanguageSettings());
  el("addAsrProfile").addEventListener("click", () => addProvider("asr"));
  el("addTranslationProfile").addEventListener("click", () => addProvider("translation"));
  el("subtitlesEnabled").addEventListener("change", () => { subtitlePrefs.enabled = el("subtitlesEnabled").checked; localStorage.setItem("lingerlens.subtitle.enabled", String(subtitlePrefs.enabled)); if (!subtitlePrefs.enabled) clearSubtitle(); });
  el("subtitleMode").addEventListener("change", () => { subtitlePrefs.mode = el("subtitleMode").value; localStorage.setItem("lingerlens.subtitle.mode", subtitlePrefs.mode); applySubtitlePrefs(); });
  el("subtitleSize").addEventListener("change", () => { subtitlePrefs.size = el("subtitleSize").value; localStorage.setItem("lingerlens.subtitle.size", subtitlePrefs.size); applySubtitlePrefs(); });
  el("subtitleOffset").addEventListener("input", updateSubtitleOffset);
  el("url").addEventListener("keydown", (event) => { if (event.key === "Enter") probe(); });
  video.addEventListener("playing", () => {
    if (sessionAction === "stopping") return;
    setMediaLoading(false);
    setState("延迟播放中", "stable");
    updatePlayerControls();
    schedulePlayerControlsHide();
  });
  // Reset the fatal-error budget whenever the playhead actually advances: a
  // stream that recovers and plays for a while must not be permanently
  // condemned by earlier failures.
  video.addEventListener("timeupdate", () => {
    if (video.currentTime > mseRecoveryProgressAt + 0.5) {
      mseRecoveryProgressAt = video.currentTime;
      mseRecoveryAttempts = 0;
    }
  });
  video.addEventListener("pause", () => { updatePlayerControls(); revealPlayerControls(); });
  video.addEventListener("volumechange", updatePlayerControls);
  video.addEventListener("waiting", () => {
    if (sessionAction === "stopping" || lastSessionState !== "running") return;
    setMediaLoading(true, "播放器正在缓冲…");
    setState("播放器缓冲", "waiting");
    revealPlayerControls();
  });

  // Live chat translation toggle
  const chatToggle = el("chatTranslateToggle");
  if (chatToggle && liveMessagesClient) {
    chatToggle.addEventListener("change", async () => {
      try {
        const previous = !chatToggle.checked;
        localStorage.setItem("lingerlens.liveMessages.translate", String(chatToggle.checked));
        try {
          await liveMessagesClient.setTranslate(chatToggle.checked);
        } catch (error) {
          chatToggle.checked = previous;
          localStorage.setItem("lingerlens.liveMessages.translate", String(previous));
          throw error;
        }
      } catch (err) {
        console.warn("Failed to set chat translate setting:", err);
      }
    });
  }

  // Clear timelines buttons
  el("clearSubtitlesTimelineBtn")?.addEventListener("click", () => {
    subtitleCues.clear();
    renderSubtitlesTimeline(null);
  });
  el("clearChatTimelineBtn")?.addEventListener("click", () => {
    liveMessagesTimeline?.clear();
  });

  applySubtitlePrefs();
  subtitleWindow?.apply();
  // Narrow browser-test seam for the production renderer. The public UI still
  // mutates cues only through polling; tests use these references to verify
  // concurrent speaker rows without duplicating rendering implementation.
  window.__lingerlensSubtitleCues = subtitleCues;
  window.__lingerlensRenderSubtitle = renderSubtitle;
  window.__lingerlensRenderTimelines = renderTimelines;
  /* 与上面两个同类的测试钩子：诊断栏只在真出错时才动，没有它就只能靠制造
     一次真实故障来验证展开路径。 */
  window.__lingerlensDiagnostics = {
    log: diagnosticsLog,
    bar: diagnosticsBar,
    client: diagnosticsClient,
    devLog: devLogClient,
    toggleDevLog,
    flushDevLog,
  };
  window.__lingerlensSetSubtitleTestWallTime = (value) => {
    window.__lingerlensSubtitleTestWallTime = Number.isFinite(Number(value)) ? Number(value) : null;
  };
  /*
   * 长时间实测的采样钩子。要回答「这条字幕有没有在对的时间出现在屏幕上」，
   * 必须同时拿到两样东西：**屏幕上的字**（DOM 里真正渲染出来的行）和**那一刻
   * 的媒体时钟**。前者可以从外面读 DOM，后者不行——`mediaClock` 只在渲染进程
   * 里。所以这里把两者一次性交出去，让外部只管轮询，不必复制任何渲染逻辑。
   *
   * 只读：不写状态、不触发渲染。返回的是快照。
   */
  window.__lingerlensSoakProbe = () => {
    const layer = el("subtitleLayer");
    const content = layer?.querySelector(".subtitle-content");
    return {
      at: Date.now() / 1000,
      wall: playingWallClock(),
      currentTime: Number.isFinite(video?.currentTime) ? video.currentTime : null,
      paused: video?.paused ?? null,
      playbackRate: video?.playbackRate ?? null,
      readyState: video?.readyState ?? null,
      sessionState: lastSessionState,
      // 图层带 off 类时说明这一帧什么都没显示——这是"字幕空窗"的权威信号，
      // 比"行数为 0"更准，因为它区分了"清空了"和"从没渲染过"。
      hidden: layer ? layer.classList.contains("off") : null,
      rows: content ? [...content.children].map((row) => ({
        id: row.dataset.cueId || null,
        src: row.querySelector(".subtitle-src")?.textContent || "",
        zh: row.querySelector(".subtitle-zh")?.textContent || "",
      })) : [],
    };
  };

  loadLanguageSettings();
  updateFullscreenControl();
  revealPlayerControls();
  statusPoller = window.createSerialPoller?.({
    run: refreshStatus,
    intervalMs: 1000,
    hiddenIntervalMs: 5000,
    isHidden: () => document.hidden,
  });
  subtitlePoller = window.createSerialPoller?.({
    run: refreshSubtitles,
    intervalMs: 500,
    hiddenIntervalMs: 2000,
    isHidden: () => document.hidden,
  });
  /* 后端日志不是实时数据，1 秒够用；隐藏时进一步降频。 */
  diagnosticsPoller = window.createSerialPoller?.({
    run: async () => { await diagnosticsClient?.poll(); await flushDevLog(); },
    intervalMs: 1000,
    hiddenIntervalMs: 5000,
    isHidden: () => document.hidden,
  });
  statusPoller?.start();
  subtitlePoller?.start();
  diagnosticsPoller?.start();
  subtitleRenderTimer = setInterval(() => { if (!document.hidden) renderSubtitle(); }, 100);
  timelineRenderTimer = setInterval(() => { if (!document.hidden) { renderTimelines(); updatePlayerControls(); } }, 250);
  if (liveMessagesClient) liveMessagesClient.startPolling(500);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      statusPoller?.wake();
      subtitlePoller?.wake();
      diagnosticsPoller?.wake();
      renderSubtitle();
      renderTimelines();
      updatePlayerControls();
    }
  });
  /* 级别标签与复制文案都要跟着界面语言走。 */
  el("localeSwitch")?.addEventListener("change", () => diagnosticsBar?.refreshLocale());
  window.addEventListener("beforeunload", () => {
    statusPoller?.stop();
    subtitlePoller?.stop();
    diagnosticsPoller?.stop();
    clearInterval(subtitleRenderTimer);
    clearInterval(timelineRenderTimer);
    if (liveMessagesClient) liveMessagesClient.stopPolling();
  });
})();
