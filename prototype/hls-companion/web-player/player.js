(() => {
  const el = (id) => document.getElementById(id);
  const video = el("video");
  const stage = document.querySelector(".player-stage");
  const state = document.querySelector(".state-badge");
  const liveChip = el("liveChip");
  let hls = null;
  let lastPlaylistUrl = null;
  const initialParams = new URLSearchParams(location.search);
  let authToken = initialParams.get("authToken");
  let controlsBusy = false;
  let sessionAction = null;
  let lastSessionState = "idle";
  // In-flight /api/stop. Stop itself no longer waits on it (the local teardown
  // is what the click means), but a new Start must not race the previous
  // session's cleanup on the server.
  let pendingStop = null;
  // Set by Stop, cleared only by an explicit Start. While it is set the status
  // poll must not act on server truth: during backend teardown the server still
  // reports state=running with a playlist URL, and re-attaching it here would
  // restart the very playback the user just stopped (observed live: the picture
  // came back for the ~2s the teardown took).
  let stopRequested = false;
  // Which media session the local stop is about, and which one the server last
  // described. The barrier above may only be released by evidence that the
  // stopped session is gone: a sample that says idle, or a DIFFERENT session
  // that started afterwards. Without an identity the only signal is the idle
  // sample, and a poll can legitimately never see one -- teardown can finish
  // between two samples, or another tab can claim the next session first. That
  // is the latch this replaces: one Start that failed and the page never
  // attached again.
  let observedMediaSessionId = null;
  let stoppedMediaSessionId = null;
  // Bumped by every local session action (Stop, Start). A status response that
  // was already in flight when this changed describes a world the page has left,
  // so it must not touch the UI or attach a playlist.
  let uiGeneration = 0;
  // One-shot: the barrier is being held only because the backend cannot say
  // which session it is describing.
  let stopUnconfirmedLogged = false;
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
  let mseRecoveryTimer = null;
  let mseRecoveryProgressWall = null;
  let mseRecoveryPlayed = 0;
  let subtitleCues = new Map();
  let subtitleCueOrderDirty = true;
  let subtitleDraft = null;
  let sortedSubtitleCues = [];
  let lastSubtitlePaintKey = null;
  const subtitleReadiness = new Map();
  let subtitleAfterSeq = 0;
  let subtitleMaxKnownEnd = 0;
  // Paint against the presented video frame. No positive timing lookahead:
  // neither a timer interval nor network latency changes where a cue belongs.
  const SUBTITLE_RENDER_ADVANCE_SECONDS = 0;
  // A draft is cut at the playhead the backend was told about, which the status
  // poll refreshes once a second. This absorbs that gap, and hides the line when
  // a seek or a stalled poll leaves it behind.
  const SUBTITLE_DRAFT_STALE_SECONDS = 2.5;
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
        diagnosticsBar?.push("warn", "backend", updateLabel("diag.devlog.overwritten", "后端日志环形缓冲已覆盖 {n} 条较早记录", { n: data.missed }));
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
  // 请求画像必须在任何播放开始之前装好，否则抓不到 HLS 的分片请求。
  const netProbe = window.LingerLensDiagnostics?.createNetProbe?.({ enabled: () => Boolean(devLogClient?.get()?.enabled) });
  const playbackProbe = window.LingerLensDiagnostics?.createPlaybackProbe({
    video,
    enabled: () => Boolean(devLogClient?.get()?.enabled),
    hidden: () => document.hidden,
    /*
     * 弹幕的成本要分四层记，因为 workMs 只看得见第一层：
     *   JS    —— workMs 里的 chatOverlay / chatHistory（已知它很小）
     *   DOM   —— gauges 里的节点数、动画数、store 条数
     *   布局  —— reflow 次数与耗时（append 之后那次 getBoundingClientRect）
     *   合成  —— jank 里的帧间隔直方图
     * 前三层直接决定第四层；只有把四层并排放，才能说清"画面为什么掉帧"。
     * 计数器是累计值，探针自己按窗口做差。
     */
    counters: () => ({
      poll: liveMessagesClient?.getCounters?.().poll || 0,
      got: liveMessagesClient?.getCounters?.().got || 0,
      added: liveMessagesClient?.getCounters?.().added || 0,
      launch: chatOverlay?.getStats?.().launched || 0,
      drop: chatOverlay?.getStats?.().dropped || 0,
      reflow: chatOverlay?.getStats?.().reflows || 0,
      reflowMs: Math.round(chatOverlay?.getStats?.().reflowMs || 0),
      rewrite: liveMessagesTimeline?.getStats?.().rewrites || 0,
    }),
    gauges: () => ({
      // on / tl 是 A/B 的记账凭证：没有它们，两份日志分不出哪场关了弹幕。
      on: chatOverlayToggle.checked,
      tl: Boolean(el("chatTimelineList")?.offsetParent),
      overlay: el("chatOverlay")?.children.length || 0,
      timeline: el("chatTimelineList")?.children.length || 0,
      anim: el("chatOverlay")?.getAnimations?.({ subtree: true }).length || 0,
      store: liveMessagesClient?.getStore?.().size || 0,
    }),
    net: netProbe,
    /*
     * 每五秒一条的机器遥测，直接落盘，不进诊断栏的环形缓冲。
     *
     * 那个缓冲是给人读的：单条限长 400 字符，总共 400 格。playback-perf 一小时
     * 720 条，两头都不合适——它会把真正的 error/warn 挤出缓冲（约 33 分钟后
     * 面板里就只剩遥测了），而它自己又会被那个 400 字符的限长从中间截断，
     * 弹幕计数器正好排在截断点之后，整段丢掉。它没有一行是给人当场看的，
     * 所以只留文件这一条路。
     */
    emit: data => {
      if (!devLogClient?.get()?.enabled) return;
      const [line] = window.LingerLensDiagnostics.formatRecordLines(
        [{ t: Date.now() / 1000, level: "info", source: "playback-perf", message: JSON.stringify(data) }]);
      if (line) void devLogClient.send([line]);
    },
  });
  /* 游标用插入序 order，不用数组下标也不用时间戳：环形缓冲会淘汰旧记录，而且
     后端记录的时间戳可能落在已经交出去的记录之前，两者都会漏。 */
  let devLogCursor = 0;
  let devLogAlerted = false;
  /* Two callers flush this: the poll loop every second, and showError the
     instant a fatal error lands. Both read the cursor BEFORE awaiting the send,
     so without this guard they select the same records and write them twice --
     which is exactly how the 2026-09-16 log ended up with two identical
     `hls.js fatal` lines one second apart from a single hls.js error. Duplicated
     lines in the one artifact meant for after-the-fact diagnosis are worse than
     a delayed line. */
  let devLogFlushing = false;

  async function flushDevLog() {
    if (devLogFlushing) return 0;
    if (!devLogClient || !diagnosticsLog || !devLogClient.get()?.enabled) return 0;
    const pending = diagnosticsLog.list()
      .filter((entry) => entry.order > devLogCursor)
      .sort((a, b) => a.order - b.order);
    if (!pending.length) return 0;
    devLogFlushing = true;
    try {
      const written = await devLogClient.send(window.LingerLensDiagnostics.formatRecordLines(pending));
      // 主进程每次有上限，只接受前 N 条；游标只能推进到真正落盘的那一条，
      // 否则剩下的记录就永久丢了。
      if (written > 0) devLogCursor = pending[Math.min(written, pending.length) - 1].order;
      return written;
    } finally {
      devLogFlushing = false;
    }
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

  /** A catalog entry's display name. The built-ins are named in Chinese by the
   *  backend, so one that still carries the shipped name resolves through
   *  `profile.<id>`. A record the viewer renamed is theirs: translating it back
   *  into the catalog's wording would rename something they named on purpose. */
  function profileText(id, fallback) {
    const plain = fallback || id || "";
    const shipped = window.I18N?.catalogName?.(id);
    if (!shipped || plain !== shipped) return plain;
    return updateLabel(`profile.${id}`, plain);
  }

  let lastUpdateSignature = null;
  let updateProgressTimer = null;

  function stopUpdateProgress() {
    if (updateProgressTimer) clearInterval(updateProgressTimer);
    updateProgressTimer = null;
  }

  function renderUpdate(state, forceLabelRefresh = false) {
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
    const button = el("appUpdateButton");
    if (!button) return;
    // Source/browser mode has no installer to run. Keep the product action
    // visible only in the packaged desktop build where it can work.
    if (state?.packaged !== true) {
      button.hidden = true;
      return;
    }
    if (signature === lastUpdateSignature && !forceLabelRefresh) {
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
      if (!forceLabelRefresh) diagnosticsBar?.push("info", "update",
        updateLabel("diag.update.found", "发现新版本 {version}（当前 {current}）",
          { version, current: state?.version || "" }));
    } else if (update.status === "downloading") {
      button.disabled = true;
      button.textContent = updateLabel("diag.update.downloading", "下载中 {percent}%", { percent: 0 });
      updateProgressTimer = setInterval(() => { void updateClient?.status().then(renderUpdate); }, 1000);
    } else if (update.status === "ready" || update.status === "installing") {
      button.disabled = true;
      button.textContent = updateLabel("diag.update.restarting", "正在重启并安装…");
      if (!forceLabelRefresh) diagnosticsBar?.push("info", "update", updateLabel("diag.update.ready", "更新已下载并校验通过，即将重启安装"));
    } else if (update.status === "failed") {
      button.textContent = updateLabel("diag.update.retry", "重试检查更新");
      // 检查失败（离线、公司网络、还没有 release）是常态，不该刷屏。
      if (!forceLabelRefresh && update.error && update.error !== "no fetch available") {
        diagnosticsBar?.push("warn", "update", updateLabel("diag.update.failed", "检查更新失败：{error}", { error: update.error }));
      }
    } else if (update.status === "current") {
      button.textContent = updateLabel("diag.update.current", "已是最新版本");
    } else {
      button.textContent = updateLabel("diag.update.check", "检查更新");
    }
  }

  updateClient = window.LingerLensDiagnostics?.createUpdateClient({ onUpdate: renderUpdate }) || null;
  el("appUpdateButton")?.addEventListener("click", async () => {
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
  document.addEventListener("i18n:changed", () => {
    renderUpdate(updateClient?.get(), true);
    renderDevLogState(devLogClient?.get());
    // 模型设置/登录 Cookie 两个弹窗里的字段是拼出来的 HTML，不重新渲染就会
    // 停在切换前那门语言。未保存的编辑都在 providerCatalog 里（输入即写入），
    // 所以重渲染不会丢。
    renderProviderProfiles();
    renderEffectiveConfig();
    updateCookiePlatformHelp();
    renderRoleModeHint();
  });
  function providerHasCredential(provider) {
    return provider?.apiKeyConfigured === true
      || Boolean(provider?.apiKey && provider.apiKey !== "***");
  }

  function providerOption(provider, selected = false) {
    const tier = provider.readiness?.tier;
    // 只标「暂不可用」。候选/实验是内部的发布档位，写进下拉框只会让用户以为
    // 自己选了个半成品。
    const suffix = tier === "blocked" ? ` [${updateLabel(`provider.tier.${tier}`, tier)}]` : "";
    const label = profileText(provider.id, provider.label || provider.model || provider.id) + suffix;
    const detail = provider.model && !String(provider.label || "").includes(provider.model)
      ? ` · ${provider.model}`
      : "";
    // The star is the interface's to draw, not the name's: the catalog labels
    // carry no star, so this is the one and only one a line can show.
    const mark = isRecommended(provider) ? "⭐ " : "";
    return `<option value="${escapeHtml(provider.id)}"${selected ? " selected" : ""}${tier === "blocked" ? " disabled" : ""}>${escapeHtml(mark + label)}${escapeHtml(detail)}</option>`;
  }

  function roleProviders(group, references = []) {
    const referenced = new Set(references.filter(Boolean));
    return recommendedFirst(
      (group?.providers || []).filter(
        (provider) => providerHasCredential(provider) || referenced.has(provider.id),
      ),
    );
  }

  /*
   * 推荐档位只有一个：Soniox。它在同一条会话里直接出双语字幕，不需要再配翻译
   * 模型，所以排在列表最前面、带星标和「推荐」徽标。千问 LiveTranslate 走的是
   * 同一类端到端协议，但星只给一颗——两个标记并列时用户读到的不是「更推荐」，
   * 而是「这俩一样」，那还不如不标。
   *
   * 判据是「协议」而不是目录里的 recommended 字段：用户自己新建的 Soniox 配置
   * 和内置预设是同一个档位，只认持久化字段会让它掉到列表底部。
   */
  const RECOMMENDED_KINDS = new Set(["soniox-realtime"]);

  /*
   * 目录里存过的 recommended 是旧意见的快照：在这条规则改之前添加的千问
   * LiveTranslate 记录里就写着 true，而它已经不带星了。所以对应用已经表过态的
   * 协议，一律按协议判断；这个字段只对应用没有看法的记录（比如翻译预设）算数。
   */
  const RECOMMENDATION_DECIDED_KINDS = new Set(["soniox-realtime", "dashscope-livetranslate-realtime"]);

  function isRecommended(provider) {
    if (provider?.readiness && provider.readiness.tier !== "candidate") return false;
    if (RECOMMENDATION_DECIDED_KINDS.has(provider?.kind)) return RECOMMENDED_KINDS.has(provider?.kind);
    return provider?.recommended === true;
  }

  function recommendedFirst(providers) {
    // Array.prototype.sort 在现代引擎里是稳定的，同档位内保持目录原有顺序。
    return providers.slice().sort((a, b) => Number(isRecommended(b)) - Number(isRecommended(a)));
  }

  function recommendedBadge(provider) {
    return isRecommended(provider)
      ? `<span class="recommended-badge" title="${escapeHtml(updateLabel("dlg.model.recommendedHint", "同一会话直接生成双语字幕，无需另配翻译模型"))}">⭐ ${escapeHtml(updateLabel("dlg.model.recommended", "推荐"))}</span>`
      : "";
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
    // Soniox/Qwen LiveTranslate can translate on the ASR session itself. In
    // that mode the ordinary subtitle translator and its fallback remain in
    // the saved catalog for when the user switches back, but they are not
    // called for the current stream. Disable both selectors so the UI says
    // exactly what will happen instead of suggesting a second model is active.
    const nativeTranslation = Boolean(languageState.translation?.native);
    for (const id of ["roleSubtitle", "roleFallback"]) {
      const select = el(id);
      const status = el(id === "roleSubtitle" ? "roleSubtitleStatus" : "roleFallbackStatus");
      if (!select) continue;
      select.disabled = nativeTranslation;
      if (nativeTranslation) {
        select.title = updateLabel(
          "msg.nativeTranslationRoleDisabled",
          "当前使用识别 Provider 的内置翻译；此处的独立字幕翻译不会被调用。",
        );
        select.setAttribute("aria-describedby", "roleModeHint");
      } else {
        select.removeAttribute("title");
        select.removeAttribute("aria-describedby");
      }
      if (status) {
        status.hidden = !nativeTranslation;
        status.textContent = nativeTranslation
          ? updateLabel(
            "msg.nativeTranslationRoleUnavailable",
            "不可用：已由识别 Provider 的内置翻译接管",
          )
          : "";
      }
    }
    const fallbackSelect = el("roleFallback");
    if (fallbackSelect) {
      const fallbackActive = fallback.find((id) => id !== translation.active) || "";
      // 和上面三个选择器用同一条规则、同一个列表。以前这里额外把当前生效的
      // Provider 滤掉，于是只配了一个翻译 Provider 时兜底永远是空的——那不像
      // 「没得选」，更像坏掉了。
      const fallbackProviders = roleProviders(translation, [fallbackActive, translation.active]);
      fallbackSelect.innerHTML = `<option value="">${updateLabel("opt.none", "不选")}</option>${fallbackProviders.map((provider) => providerOption(provider, provider.id === fallbackActive)).join("")}`;
    }
    renderRoleModeHint();
  }
  async function selectRole(id, section) {
    const controls = ["roleAsr", "roleSubtitle", "roleFallback", "roleChat"].map(el);
    controls.forEach(c => c.disabled = true);
    el("roleFeedback").textContent = updateLabel("msg.applyingSelection", "正在应用选择…");
    try {
      const value = el(id).value;
      const patch = section === "translationFallback"
        ? { translation: { fallback: value ? [value] : [] } }
        : { [section]: { active: value } };
      const data = await request("/api/providers", patch);
      renderRoleSelectors(data);
      if (section === "asr") await refreshLanguageCapabilities();
      el("roleFeedback").textContent = section === "asr"
        ? updateLabel("msg.saved", "已保存")
        : section === "translationFallback"
          ? updateLabel("msg.savedFallback", "已保存兜底选择")
          : updateLabel("msg.applied", "已应用");
    } catch (error) {
      if (roleCatalog) renderRoleSelectors(roleCatalog);
      el("roleFeedback").textContent = updateLabel("msg.switchFailed", `切换失败：${error.message}`, { error: error.message });
    } finally { controls.forEach(c => c.disabled = false); }
  }
  // Language catalog/capability state from /api/languages (server-authoritative)
  // plus the persisted subtitle preferences used as the persistence merge base.
  const languageState = { asr: null, translation: null, subtitle: null, candidates: [] };
  let sourceSelector = null;
  let targetSelector = null;
  let candidateSelector = null;

  function renderRoleModeHint() {
    const node = el("roleModeHint");
    if (!node) return;
    if (languageState.translation?.native) {
      node.textContent = updateLabel(
        "msg.nativeBilingualMode",
        "当前字幕模式：识别 Provider 内置双语；不调用独立翻译模型。画面是否显示两行，由“字幕显示”中的“显示”决定。",
      );
      node.dataset.tone = "native";
    } else if (languageState.translation) {
      node.textContent = updateLabel(
        "msg.separateTranslationMode",
        "当前字幕模式：语音识别 + 独立翻译。这里的“字幕翻译” Provider 会接收识别结果；画面是否显示两行，由“字幕显示”中的“显示”决定。",
      );
      node.dataset.tone = "separate";
    } else {
      node.textContent = "";
      delete node.dataset.tone;
    }
  }
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
  /*
   * 透明度滑块的写入路径。
   *
   * 原来每个 input 事件都做两件事：改 .chat-overlay 的 CSS 变量，外加一次**同
   * 步的 localStorage 落盘**。拖一次滑块就是几十次落盘；而变量消费方是容器上
   * 的 opacity，容器一旦 opacity<1 就成为一个不透明组，里面几十个正在做
   * transform 动画的弹幕会被当作一个整体反复重新光栅化。
   *
   * 现在三件事：
   *   1. 写入按帧合并——一帧最多写一次，而不是每个 input 一次；
   *   2. localStorage 挪到松手/失焦，拖动过程中不落盘；
   *   3. 指针按住期间给弹幕层加 .adjusting 冻结动画，让这次交互作用在静止的树
   *      上；松手恢复。类的名字不能复用 .paused，那个每 250ms 会被覆写。
   */
  let chatOpacityFrame = 0;
  const applyChatOpacity = () => el("chatOverlay").style.setProperty("--chat-opacity", chatOverlayOpacity.value);
  const flushChatOpacity = () => {
    if (chatOpacityFrame) {
      cancelAnimationFrame(chatOpacityFrame);
      chatOpacityFrame = 0;
    }
    applyChatOpacity();
  };
  const scheduleChatOpacity = () => {
    if (chatOpacityFrame) return;
    chatOpacityFrame = requestAnimationFrame(() => {
      chatOpacityFrame = 0;
      applyChatOpacity();
    });
  };
  const endChatOpacityAdjust = () => {
    el("chatOverlay").classList.remove("adjusting");
    flushChatOpacity();
    localStorage.setItem("lingerlens.chatOverlay.opacity", chatOverlayOpacity.value);
  };
  applyChatOpacity();
  chatOverlayOpacity.addEventListener("input", scheduleChatOpacity);
  chatOverlayOpacity.addEventListener("pointerdown", () => el("chatOverlay").classList.add("adjusting"));
  chatOverlayOpacity.addEventListener("keydown", () => el("chatOverlay").classList.add("adjusting"));
  for (const event of ["pointerup", "pointercancel", "change", "blur"]) {
    chatOverlayOpacity.addEventListener(event, endChatOpacityAdjust);
  }
  el("chatOverlay").style.setProperty("--chat-size", chatOverlaySize.value);
  chatOverlayToggle.addEventListener("change", () => {
    localStorage.setItem("lingerlens.chatOverlay.enabled", String(chatOverlayToggle.checked));
    chatOverlay.clear();
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
  const subtitleBudget = { lowSince: null, suggested: null, primed: false };
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
  if (el("proxyMode")) {
    const savedProxyMode = localStorage.getItem("lingerlens.proxy.mode");
    el("proxyMode").value = ["direct", "system", "manual"].includes(savedProxyMode)
      ? savedProxyMode
      : (el("proxy").value ? "manual" : "system");
  }

  function updateProxyModeUi() {
    const mode = el("proxyMode")?.value || "system";
    const manual = mode === "manual";
    const field = el("proxyManualField");
    if (field) field.hidden = !manual;
    if (!manual && el("proxy")) el("proxy").setCustomValidity("");
    if (el("proxyMode")) localStorage.setItem("lingerlens.proxy.mode", mode);
  }
  updateProxyModeUi();
  el("targetDelay").value = String(savedTargetDelay > 10 && savedTargetDelay <= 60 ? savedTargetDelay : 15);

  const setState = (text, tone = "idle") => {
    el("stateText").textContent = text;
    state.dataset.tone = tone;
    // 顶栏 LIVE 徽章跟随会话状态：只有真正在跑直播时才亮。
    if (liveChip) liveChip.hidden = tone === "idle" || tone === "error";
  };

  const seconds = (value) => Number.isFinite(value) ? `${value.toFixed(1)} ${updateLabel("unit.seconds", "秒")}` : "—";
  const integer = (value) => Number(value || 0).toLocaleString("zh-CN");
  /* 费用必须按它自己的币种显示。币种由后端随 Provider 一起给出（中国厂商
     人民币，其余美元），前端不做任何换算——没有汇率就不会算错。 */
  const CURRENCY_SYMBOLS = { CNY: "¥", USD: "$" };
  /* Number(null) 是 0，所以「有没有值」必须显式判断：以前用
     Number.isFinite(Number(value)) 会把「不可估算」判成 0 元，把 ¥0.0000
     当成一个真实金额显示出来。 */
  const hasCost = (value) => value !== null && value !== undefined && value !== ""
    && Number.isFinite(Number(value));
  const money = (value, currency) => {
    const amount = Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 4, maximumFractionDigits: 6 });
    const code = String(currency || "").toUpperCase();
    const symbol = CURRENCY_SYMBOLS[code];
    // 币种缺失时宁可只给数字，也不默认成人民币——猜错币种比不写币种更糟。
    return symbol ? `${symbol}${amount}` : code ? `${amount} ${code}` : amount;
  };
  /* 只显示结论，不显示后端给的原因。原因是一句英文诊断（例如
     "subtitle usage unavailable while subtitles are not running"），
     放在卡片正面是噪音；它改由 title 悬停提示承载。 */
  const costText = (value, currency) => (hasCost(value)
    ? money(value, currency)
    : updateLabel("cost.notEstimable", "不可估算"));
  /* 主力与兜底可能是不同币种，这时没有「一个」合计，逐币种列出。 */
  const costsText = (entries) => (Array.isArray(entries) && entries.length
    ? entries.map((entry) => money(entry.amount, entry.currency)).join(" + ")
    : costText(null, null));

  async function request(path, body) {
    const timeout = path === "/api/probe" ? 20000 : 30000;
    const controller = new AbortController();
    let timer;
    let response;
    let text;
    try {
      const result = await Promise.race([
        (async () => {
          const response = await fetch(path, {
            method: body ? "POST" : "GET",
            headers: body ? { "Content-Type": "application/json" } : undefined,
            body: body ? JSON.stringify(body) : undefined,
            cache: "no-store",
            signal: controller.signal,
          });
          return { response, text: await response.text() };
        })(),
        new Promise((_, reject) => {
          timer = setTimeout(() => {
            controller.abort();
            reject(new Error(path === "/api/probe"
              ? "读取直播信息超时（20 秒）。请确认代理软件正在运行，或检查直播是否需要登录 Cookie。"
              : "本地后台响应超时，请重试。"));
          }, timeout);
        }),
      ]);
      response = result.response;
      text = result.text;
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
    const mode = el("proxyMode")?.value || "system";
    body.proxyMode = mode;
    if (mode === "manual") {
      const proxy = el("proxy")?.value.trim() || "";
      if (!proxy) throw new Error("请选择代理地址，或把网络连接方式改为直连/系统代理。");
      body.proxy = proxy;
      localStorage.setItem("lingerlens.proxy", proxy);
    } else if (mode === "direct") {
      localStorage.removeItem("lingerlens.proxy");
    }
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
    el("probe").textContent = updateLabel("action.probing", "解析中…");
    el("probe").setAttribute("aria-busy", "true");
    el("setupFeedback").textContent = "正在读取直播信息和可用清晰度…";
    setBusy(true);
    setState("正在准备", "waiting");
    el("message").textContent = "正在准备直播…";
    try {
      const data = await request("/api/probe", commonBody());
      const select = el("quality");
      select.innerHTML = `<option value="auto">${updateLabel("quality.autoBest", "自动（最高兼容）")}</option>`;
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
      el("setupFeedback").textContent = "解析完成，选择清晰度后点击开始播放。";
      el("message").textContent = "直播已就绪。";
      setState("准备就绪", "active");
    } catch (error) {
      showError(error);
    } finally {
      el("probe").textContent = updateLabel("action.probe", "解析");
      el("probe").removeAttribute("aria-busy");
      setBusy(false);
    }
  }

  async function start() {
    // This page owns the local session state again; anything older is stale.
    const claim = ++uiGeneration;
    setSessionAction("starting");
    setMediaLoading(true, "正在启动直播…");
    setBusy(true);
    setState("启动合流", "waiting");
    destroyPlayer();
    try {
      // A previous Stop may still be tearing the old session down on the
      // server. Wait for that claim to settle before starting a new one.
      if (pendingStop !== null) await pendingStop;
      // A Stop that arrived while we waited owns the UI now. Starting a session
      // on top of it would resurrect what the user just dismissed.
      if (claim !== uiGeneration) return;
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
      // A later Stop already owns the UI: this response describes a session the
      // user has stopped, so it must not clear the barrier that Stop raised.
      if (claim !== uiGeneration) return;
      // Only a session this page actually claimed clears the local stop.
      stopRequested = false;
      stoppedMediaSessionId = null;
      stopUnconfirmedLogged = false;
      // Record which session this page is now watching, taken from the start
      // response itself, so the very next poll has an identity to compare.
      observedMediaSessionId = data.status?.mediaSessionId ?? observedMediaSessionId;
      lastSessionState = data.status?.state || "running";
      localStorage.setItem("lingerlens.targetDelaySeconds", String(body.targetDelaySeconds));
      el("stop").disabled = false;
      const quality = data.quality;
      el("resolution").textContent = `${quality.width || "?"}×${quality.height || "?"}${quality.fps ? ` @ ${quality.fps}fps` : ""}`;
      el("message").textContent = updateLabel("msg.buffering", "正在建立播放缓冲，画面就绪后自动播放。");
      setState("建立延迟缓冲", "waiting");
      setMediaLoading(true, "正在建立直播缓冲…");
      authToken = null;
    } catch (error) {
      // Report the failure, but do NOT drop the identity of the session the user
      // stopped: a later, different session releases that barrier, not this
      // page's failure to start one. This is the latch being removed -- the old
      // code cleared the barrier only on a SUCCESSFUL start, so one failed Start
      // meant the poll skipped attach forever.
      if (claim !== uiGeneration) return;
      lastSessionState = "idle";
      setMediaLoading(false);
      showError(error);
    } finally {
      // Only the current claim may clear these. A Stop that happened since has
      // already restored the stopped UI and cleared busy itself, so clearing
      // them again from a stale Start would fight the newer action.
      if (claim === uiGeneration) {
        setSessionAction(null);
        setBusy(false);
      }
    }
  }

  function resetStoppedUi() {
    destroyPlayer();
    lastSessionState = "idle";
    setSessionAction(null);
    setMediaLoading(false);
    stage.classList.remove("has-media");
    el("quality").disabled = true;
    el("quality").innerHTML = `<option value="auto">${updateLabel("quality.autoBest", "自动（最高兼容）")}</option>`;
    el("start").disabled = true;
    el("setupPlayback").hidden = true;
    el("setupFeedback").textContent = "已停止。可以重新解析，或粘贴另一场直播的链接。";
    el("stop").disabled = true;
    el("streamTitle").textContent = "等待直播地址";
    el("message").textContent = "已停止当前直播。";
    for (const id of ["hiddenDelay", "playerDelay", "totalDelay", "buffer", "resolution", "uptime", "subtitleProviderStatus", "asrUsageCost", "translationUsageCost", "totalUsageCost", "translationLatency", "subtitleReadyLag", "budgetMargin", "cueDuration", "timingSources", "schedulerDrops"]) {
      el(id).textContent = "—";
    }
    setState("已停止", "idle");
  }

  /* 停止必须是「点击即生效」的本地动作，而不是一次往返。
     以前 stop() 先 setSessionAction("stopping") 再 await /api/stop，画面、
     缓冲和状态徽标全都等后端清理结束才动。用户看到的就是「停止键要按十几
     秒」。现在点击立刻拆本地播放器（hls.destroy + video.load 断开缓冲、
     状态回到已停止），后端清理在后台继续跑，只把「这一次清理还没结束」记在
     pendingStop 里；下一次 start() 会等它落定，避免新会话被上一次的清理撞上
     （服务端 /api/start 自己也会先完整停止旧会话，这里是第二道保险）。
     后端若失败也不回滚界面：直播已经不再播放，弹错只会让用户以为停止失败，
     所以只进诊断栏。 */
  function stop() {
    const alreadyStopped = lastSessionState === "idle" && !hls && !video.src;
    // A repeated click is the same intent, so only the click that actually takes
    // ownership of the stop request bumps the generation. Bumping on every click
    // would make the FIRST click's own confirmation arrive "stale" -- the claim
    // it recorded would no longer match -- and the barrier would stay up forever
    // with nothing left to release it. That is the same latch this change
    // removes, so the counter must distinguish Stop from Start, not click from
    // click.
    const ownsRequest = pendingStop === null;
    if (ownsRequest) ++uiGeneration;
    // Remember WHICH session is being stopped, so a later poll can tell it apart
    // from one that starts afterwards.
    stoppedMediaSessionId = observedMediaSessionId;
    stopRequested = true;
    resetStoppedUi();
    // The local stop owns the controls again. Without this, a Stop that lands
    // while a Start is still in flight leaves every control disabled forever,
    // because the stale Start's finally block no longer runs.
    setBusy(false);
    if (alreadyStopped && ownsRequest) return;
    if (ownsRequest) {
      const claim = uiGeneration;
      pendingStop = request("/api/stop", {})
        .then(() => {
          // A successful response is the server confirming this stop finished,
          // so release the barrier here as well rather than depending on ever
          // catching an idle sample. The claim check keeps a Start that has
          // happened since in charge of its own barrier.
          if (claim === uiGeneration) {
            ++uiGeneration;
            stopRequested = false;
            observedMediaSessionId = null;
            stoppedMediaSessionId = null;
            stopUnconfirmedLogged = false;
          }
        })
        .catch((error) => {
          // 本地已经停止；这里只留诊断痕迹，不打扰用户。A timeout is NOT the
          // server saying it stopped, so the barrier stays up.
          diagnosticsBar?.push("warn", "ui", updateLabel("err.stopCleanupUnconfirmed", "停止清理未确认：{error}", { error: error.message || error }));
        })
        .finally(() => { pendingStop = null; });
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
      });
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (!data.fatal) return;
        // Ignore errors from a player instance this session has already replaced.
        if (hls !== player) return;
        if (mseRecoveryTimer !== null) return;
        const errorType = data.type === Hls.ErrorTypes.NETWORK_ERROR ? "network"
          : data.type === Hls.ErrorTypes.MEDIA_ERROR ? "media"
          : null;
        if (errorType === null) {
          showError(new Error(`hls.js fatal: ${describeFatal(data)}`));
          return;
        }
        const decision = window.decideMseErrorRecovery({
          errorType,
          attemptsInWindow: mseRecoveryAttempts,
        });
        if (decision.action === "give-up") {
          showError(new Error(updateLabel("err.playUnrecoverable", "播放无法恢复：{reason}（{details}）", { reason: decision.reason, details: data.details })));
          return;
        }
        mseRecoveryAttempts += 1;
        mseRecoveryPlayed = 0;
        // Rebuild/reload always costs a rebuffer, so space attempts out instead
        // of re-entering the failure immediately.
        mseRecoveryTimer = window.setTimeout(() => {
          mseRecoveryTimer = null;
          if (!hls || hls !== player) return;
          if (decision.action === "recover-media") hls.recoverMediaError();
          else if (decision.action === "swap-codec") {
            hls.swapAudioCodec();
            hls.recoverMediaError();
          }
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

  function notePlaybackProgress() {
    const position = Number(video.currentTime);
    const now = performance.now();
    const advance = position - mseRecoveryProgressAt;
    const elapsed = mseRecoveryProgressWall === null ? 0 : (now - mseRecoveryProgressWall) / 1000;
    mseRecoveryProgressAt = position;
    mseRecoveryProgressWall = now;
    const plausible = elapsed > 0 && advance > 0
      && advance <= elapsed * Math.max(1, video.playbackRate || 1) * 1.5 + 0.25;
    if (!video.paused && !video.seeking && video.readyState >= 2 && plausible) {
      mseRecoveryPlayed += Math.min(advance, elapsed);
      if (mseRecoveryPlayed >= 0.5 && mseRecoveryTimer !== null) {
        window.clearTimeout(mseRecoveryTimer);
        mseRecoveryTimer = null;
      }
      // A seek is not a recovery, and one short decoded burst must not grant
      // an unlimited number of rebuilds to a repeatedly failing stream.
      if (mseRecoveryPlayed >= 2) mseRecoveryAttempts = 0;
    } else {
      mseRecoveryPlayed = 0;
    }
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
    if (mseRecoveryTimer !== null) window.clearTimeout(mseRecoveryTimer);
    mseRecoveryTimer = null;
    if (hls) hls.destroy();
    hls = null;
    lastPlaylistUrl = null;
    latestLevelDetails = null;
    sourceWasStalled = false;
    lastRecoverySeekAt = -Infinity;
    mseRecoveryAttempts = 0;
    mseRecoveryProgressAt = 0;
    mseRecoveryProgressWall = null;
    mseRecoveryPlayed = 0;
    video.playbackRate = 1;
    subtitleCues.clear();
    subtitleCueOrderDirty = true;
    subtitleDraft = null;
    resetSubtitleBudget();
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
    const claim = uiGeneration;
    try {
      // State where the playhead is on the poll the backend already receives once
      // a second. The backend feeds a realtime ASR and has to know how far ahead
      // of the viewer it is running; it cannot derive that from HLS requests,
      // because hls.js fetches 6-15s ahead of the playhead (measured 2026-09-18).
      const playhead = mediaClock?.playingWallTime?.();
      const data = await request(Number.isFinite(playhead)
        ? `/api/status?playhead=${playhead.toFixed(3)}`
        : "/api/status");
      // A response already in flight when the user pressed Stop or Start
      // describes a world this page has left. Acting on it is exactly how a
      // stopped picture came back, and how a stale running sample reverted the
      // stopped controls.
      if (claim !== uiGeneration) return;
      const mediaSessionId = data.mediaSessionId ?? null;
      // A locally requested Stop outranks server truth until the server says the
      // session it stopped is gone. "Gone" means an idle sample OR a DIFFERENT
      // media session -- not merely a later sample, which is what teardown emits
      // while it is still running the old one.
      if (stopRequested) {
        if (pendingStop !== null) return;
        const confirmedIdle = data.state === "idle";
        const differentSession = mediaSessionId !== null
          && stoppedMediaSessionId !== null
          && mediaSessionId !== stoppedMediaSessionId;
        if (!confirmedIdle && !differentSession) {
          // Comparable means the page knows both the stopped session's identity
          // and the one the server is describing. Without both, nothing can
          // prove a new session is new -- an unknown identity is never treated
          // as a different one.
          const comparable = mediaSessionId !== null && stoppedMediaSessionId !== null;
          if (!stopUnconfirmedLogged && !comparable) {
            // Conservative path, recorded once rather than on every poll.
            stopUnconfirmedLogged = true;
            diagnosticsBar?.push(
              "warn",
              "ui",
              "停止状态未确认：无法比对媒体会话身份，新会话不会被自动接入。",
            );
          }
          return;
        }
        stopRequested = false;
        stoppedMediaSessionId = null;
        stopUnconfirmedLogged = false;
      }
      // Only an admitted sample may change either playback OR its controls.
      // A post-Stop request has the current generation but can still describe
      // the old running session until cleanup confirms.
      lastSessionState = data.state;
      renderSessionControls();
      if (observedMediaSessionId !== null && mediaSessionId !== observedMediaSessionId
          && sessionAction !== "starting") {
        ++uiGeneration;
        destroyPlayer();
      }
      observedMediaSessionId = mediaSessionId;
      const recovery = data.sessionRecovery || {};
      if (recovery.state === "reconnecting") {
        // The backend tears down the old clock before it re-probes the source.
        // Keep the page in a waiting state during that short gap instead of
        // showing a terminal FFmpeg error or resetting the setup form. A later
        // poll sees the new mediaSessionId and attaches the fresh timeline.
        resetSubtitleBudget();
        setMediaLoading(true, "正在自动恢复直播…");
        setState("正在自动恢复", "waiting");
        el("message").textContent = updateLabel("msg.reconnectingAttempt", "直播暂时中断，正在重新连接（第 {n} 次）…", { n: Number(recovery.attempts || 0) });
        return;
      }
      // While this page is starting a session the server still describes the
      // previous one, so attaching from it would reconnect the playlist being
      // replaced. The first poll after start() settles attaches normally.
      if (sessionAction === "starting") return;
      if (data.playlistUrl) attach(data.playlistUrl);
      if (data.state === "error" && sessionAction !== "stopping") {
        // The error branch throws, so the shared updateStallOverlay() call near
        // the end of this function never runs. Show the error banner here
        // rather than relying on a second, unexplained call at the top.
        resetSubtitleBudget();
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
      el("hiddenDelay").textContent = seconds(data.hiddenMediaSeconds);
      const playerBehind = estimateVideoLatency();
      el("playerDelay").textContent = seconds(playerBehind);
      // Both positions are on the local HLS media clock. Adding withheld
      // seconds to a browser edge from a different playlist refresh races them.
      const currentWall = mediaClock?.playingWallTime?.();
      const measuredDelay = data.state === "running" && Number.isFinite(data.privateEdgeWallTime)
        && Number.isFinite(currentWall) && data.privateEdgeWallTime >= currentWall
        ? data.privateEdgeWallTime - currentWall : null;
      el("totalDelay").textContent = seconds(measuredDelay);
      updateStallOverlay(data);
      if (data.state === "running" && Number(data.targetDelaySeconds) > 10) {
        el("targetDelay").value = String(data.targetDelaySeconds);
        localStorage.setItem("lingerlens.targetDelaySeconds", String(data.targetDelaySeconds));
      }
      const ahead = bufferAhead();
      el("buffer").textContent = seconds(ahead);
      updatePlaybackRecovery(data, playerBehind, ahead, measuredDelay);
      el("uptime").textContent = seconds(Number(data.uptimeSeconds));
      const subtitles = data.subtitles || {};
      el("subtitleProviderStatus").innerHTML = subtitles.asrProviderId
        ? `<span><small>ASR</small>${escapeHtml(profileText(subtitles.asrProviderId, subtitles.asrProviderLabel || subtitles.asrProviderId))}</span><span><small>${updateLabel("cost.translation", "翻译")}</small>${escapeHtml(profileText(subtitles.translationProviderId, subtitles.translationProviderLabel || subtitles.translationProviderId) || updateLabel("cost.originalOnly", "仅原文"))}</span>`
        : `<span class="usage-empty">${updateLabel("state.notRunning", "未运行")}</span>`;
      const asrUsage = subtitles.asrUsage || { seconds: subtitles.asrSeconds || 0 };
      const translationUsage = subtitles.translationUsage || {};
      el("asrUsageCost").innerHTML = `<span><small>${updateLabel("cost.audio", "音频")}</small>${Number(asrUsage.seconds || 0).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} ${updateLabel("unit.seconds", "秒")}</span><span><small>${updateLabel("cost.costLabel", "费用")}</small>${costText(subtitles.asrEstimatedCostCny, subtitles.asrCostCurrency)}</span>`;
      el("translationUsageCost").innerHTML = `<span><small>${updateLabel("cost.input", "输入")}</small>${integer(translationUsage.nonCachedInputTokens)}</span><span><small>${updateLabel("cost.cached", "缓存")}</small>${integer(translationUsage.cachedInputTokens)}</span><span><small>${updateLabel("cost.output", "输出")}</small>${integer(translationUsage.outputTokens)}</span><span class=\"usage-cost\"><small>${updateLabel("cost.costLabel", "费用")}</small>${costsText(translationUsage.costsByCurrency)}</span>`;
      el("totalUsageCost").textContent = costsText(subtitles.costsByCurrency);
      // 后端给的原因仍要能看到，只是不占卡片正面。
      for (const [id, reason] of [
        ["asrUsageCost", subtitles.asrEstimateReason],
        ["translationUsageCost", subtitles.translationEstimateReason],
        ["totalUsageCost", subtitles.totalEstimateReason],
      ]) {
        const node = el(id);
        if (node) node.title = reason || "";
      }
      const latency = subtitles.avgTranslationLatencyMs;
      el("translationLatency").textContent = Number.isFinite(latency) ? `${(Number(latency) / 1000).toFixed(2)} ${updateLabel("unit.seconds", "秒")}` : "—";
      updateSubtitleBudget(subtitles, measuredDelay, Number(data.targetDelaySeconds));
      if (data.quality) el("resolution").textContent = `${data.quality.width || "?"}×${data.quality.height || "?"}${data.quality.fps ? ` @ ${data.quality.fps}fps` : ""}`;
    } catch (error) {
      if (claim !== uiGeneration) return;
      resetSubtitleBudget();
      for (const id of ["totalDelay", "hiddenDelay", "playerDelay", "buffer", "translationLatency", "subtitleReadyLag"]) el(id).textContent = "—";
      if (!String(error.message).includes("Failed to fetch")) showError(error);
    }
  }

  function updatePlaybackRecovery(data, playerBehind, ahead, measuredDelay = null) {
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
        // The publisher's stall threshold has to clear the segment length, or it
        // measures publication jitter instead of a stall.
        targetDuration: data.targetDuration,
      })
      : {
        active: Number.isFinite(publisherStall) && publisherStall > 5,
        kind: "upstream",
        stallSeconds: publisherStall,
      };
    // Only an ingest-confirmed outage should pause catch-up. A publisher-only
    // pause is a local packaging hiccup; the player can keep draining its
    // buffer and recover without being forced into a hold state. The policy
    // takes this as a BOOLEAN: the seconds that produced it belong to the
    // classifier, and re-judging them downstream is how two rules for one
    // question drift apart. `=== true` so a non-boolean cannot pose as a stall.
    const isStalled = health.active === true && health.kind === "upstream";
    const recovered = sourceWasStalled && !isStalled;
    // Use the same media positions as the displayed local lag. The backend's
    // withheld duration and this browser's playlist may be from different ticks.
    const edgeGap = Number.isFinite(measuredDelay) && Number.isFinite(playerBehind)
      ? Math.max(0, measuredDelay - playerBehind) : data.hiddenMediaSeconds;
    const decision = window.decidePlaybackRecovery?.({
      playerBehind,
      targetDelay: data.targetDelaySeconds,
      hiddenDelay: edgeGap,
      bufferAhead: ahead,
      upstreamStalled: isStalled,
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
  /*
   * The automatic pause is not allowed to overrule the person watching.
   *
   * A status poll runs every second, and the old code re-paused the element on
   * every poll where the source was stalled and the buffer was thin. The resume
   * condition was "buffer >= 10s", which a source that never comes back can
   * never satisfy, so a stall left playback stopped and the play button
   * apparently broken: press it and the next poll paused it again. Once the
   * viewer has asked for playback, that decision stands for the rest of the
   * stall; it is cleared as soon as the source is healthy again.
   */
  let stallAutoPauseSuppressed = false;
  // Our own resume is not the viewer asking for anything.
  let autoResumeInFlight = false;
  // Only log the transition; the poll runs every second.
  let quietSourceLogged = false;
  // Buffer, in seconds ahead of the playhead, at which a stalled source starts
  // to be the viewer's problem rather than the source's.
  const STALL_VISIBLE_BUFFER_SECONDS = 3;
  // How much buffer an automatic pause waits for before it resumes on its own.
  const STALL_RESUME_BUFFER_SECONDS = 10;

  function updateStallOverlay(data) {
    const banner = el("stallBanner");
    if (!banner) return;
    if (data.state === "error") {
      autoPausedForStall = false;
      setMediaLoading(false);
      banner.hidden = false;
      const detail = String(data.error || "未知错误").slice(0, 80);
      banner.textContent = updateLabel("msg.sessionError", `直播会话出错：${detail}。请停止后重新开始播放`, { error: detail });
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
        targetDuration: data.targetDuration,
      })
      : {
        active: data.state === "running" && data.playlistReady && Number.isFinite(stall) && stall > 5,
        kind: "upstream",
        stallSeconds: stall,
      };
    const stalled = health.active;
    const ahead = bufferAhead();
    if (!stalled) stallAutoPauseSuppressed = false;

    if (autoPausedForStall) {
      /*
       * Resume as soon as the buffer can carry playback again, whether or not the
       * source has recovered.
       *
       * The buffer is the only thing that makes playing possible, so it is the
       * only thing worth waiting for. Requiring the source to be healthy too
       * holds a pause the player does not need: the long run caught it sitting on
       * a full 30s buffer, source stalled, picture frozen, banner counting
       * seconds -- a stop the viewer cannot explain and cannot clear except by
       * pressing play. (The pre-existing code had the same resume condition; it
       * was reached only when the stall had already cleared, so the pause with a
       * deep buffer was unreachable in practice. Checking the resume first made
       * it reachable, and then "and the source is healthy" kept it stuck.)
       */
      if (ahead >= STALL_RESUME_BUFFER_SECONDS) {
        autoPausedForStall = false;
        banner.hidden = true;
        if (video.paused) {
          autoResumeInFlight = true;
          video.play().catch(() => { autoResumeInFlight = false; });
        }
      } else {
        banner.hidden = false;
        banner.textContent = stalled
          ? stallBannerText(health)
          : updateLabel("msg.recovered", "直播流已恢复，正在补充播放缓冲…");
      }
      return;
    }

    /*
     * The banner reports what the VIEWER is experiencing, never what the source
     * is doing.
     *
     * "The source has been quiet for N seconds" and "the viewer has been hurt"
     * are different questions, and answering the first one is how a healthy
     * stream got a stall banner: the segments of this stream are 5.005s long, so
     * "5 seconds since the last new segment" is its normal breathing. A quiet
     * source behind a deep buffer is not merely tolerable -- it is invisible,
     * and announcing it is a false alarm the viewer cannot act on. The quiet
     * source is still worth recording, so it goes to the diagnostics bar.
     */
    const starving = ahead < STALL_VISIBLE_BUFFER_SECONDS;
    /*
     * One record per quiet-source TRANSITION, and it states only what is visible
     * right now.
     *
     * It used to say "未打扰播放" -- and then, in the same call, show the banner and
     * pause playback whenever the buffer was under three seconds, which is most of
     * the time a stall is worth reporting at all. A live session on 2026-09-17
     * wrote "直播源已停 8 秒（upstream），播放缓冲仍有 2 秒，未打扰播放" and paused
     * playback immediately afterwards: the message asserted the opposite of what
     * the code did next, which is worse than no message. The fix is not a better
     * prediction but no prediction -- the buffer size and the fact of the pause are
     * each recorded where they actually happen.
     */
    if (stalled !== quietSourceLogged) {
      quietSourceLogged = stalled;
      if (stalled) {
        diagnosticsBar?.push("warn", "source",
          updateLabel("msg.stallSummary", "直播源已停 {seconds} 秒（{kind}），当前播放缓冲 {ahead} 秒", { seconds: Math.round(health.stallSeconds), kind: health.kind, ahead: Math.round(ahead) }));
      }
    }
    if (!stalled || !starving) {
      banner.hidden = true;
      return;
    }
    banner.hidden = false;
    banner.textContent = stallBannerText(health);
    if (!video.paused && !stallAutoPauseSuppressed) {
      autoPausedForStall = true;
      video.pause();
      // Recorded AFTER it happened, so the log cannot promise a pause that some
      // later condition prevented.
      diagnosticsBar?.push("warn", "source", "缓冲不足，本次已自动暂停等待补充");
    }
  }

  function stallBannerText(health) {
    const message = health.kind === "packaging"
      ? updateLabel("msg.stallPackaging", "本地处理暂时跟不上，正在等待恢复")
      : updateLabel("msg.stallUpstream", "直播源暂时没有新数据，正在等待恢复");
    return updateLabel("msg.stalled", `${message}（已停 ${Math.round(health.stallSeconds)} 秒）`, { text: message, seconds: Math.round(health.stallSeconds) });
  }

  function estimateVideoLatency() {
    if (!Number.isFinite(video.currentTime) || video.readyState < 2) return null;
    // Recompute on every poll; currentTime changes between LEVEL_UPDATED events.
    if (Number.isFinite(latestLevelDetails?.edge)) return Math.max(0, latestLevelDetails.edge - video.currentTime);
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
    feedback.textContent = updateLabel("dlg.model.reading", "正在读取本机配置…");
    feedback.dataset.tone = "";
    if (!dialog.open) dialog.showModal();
    try {
      providerCatalog = await request("/api/model-settings");
      renderEffectiveConfig();
      renderProviderProfiles();
      feedback.textContent = "";
    } catch (error) {
      feedback.textContent = error.message || String(error);
      feedback.dataset.tone = "error";
    }
  }

  const providerKinds = {
    // Grouped in the order they should be tried, because these two do something
    // the others do not: they translate on the recognition session itself, so a
    // bilingual subtitle needs no second model and no second failure point.
    // Every protocol below them is wired from vendor docs and tested against a
    // local fake server only, which proves the request shape and nothing else.
    asr: [
      [
        "bilingual",
        [
          ["soniox-realtime", "⭐ Soniox Realtime STT（端到端双语）"],
          ["dashscope-livetranslate-realtime", "DashScope Qwen LiveTranslate（端到端双语）"],
        ],
      ],
      [
        "recognition",
        [
          ["dashscope-qwen-realtime", "DashScope Qwen Realtime"],
          ["soniox-realtime-transcribe", "Soniox Realtime STT（只做识别）"],
          ["dashscope-task-asr", "DashScope Task ASR"],
          ["openai-audio-transcriptions", "OpenAI Audio Transcriptions"],
          ["deepgram-streaming", "Deepgram Streaming"],
          ["openai-realtime-transcription", "OpenAI Realtime Transcription"],
          ["assemblyai-streaming", "AssemblyAI Streaming v3"],
          ["volcengine-sauc", "火山引擎豆包大模型流式 ASR (v3 sauc)"],
          ["elevenlabs-scribe-realtime", "ElevenLabs Scribe v2 Realtime"],
          ["speechmatics-realtime", "Speechmatics Realtime v2"],
          ["tencent-asr", "腾讯云实时语音识别"],
        ],
      ],
    ],
    translation: [
      [
        null,
        [
          ["openai-compatible", "OpenAI Compatible"],
          ["anthropic-messages", "Anthropic Messages (Claude)"],
          ["google-genai", "Google Gemini (GenerateContent)"],
        ],
      ],
    ],
  };

  // The group headings carry the recommendation, not just decoration: an
  // untested protocol listed right next to a working one reads as an equal
  // option. A section without an entry here stays one flat list.
  const providerKindGroups = {
    bilingual: ["grp.bilingualExperimental", "端到端双语（识别和翻译一步完成）"],
    recognition: ["grp.recognitionOnly", "只做识别（需再配一个翻译模型）"],
  };

  const providerDefaults = {
    // No `translationType` here: this protocol has no switch to turn
    // translating off, so the key was a control that changed nothing.
    // The global host is what the live service answers on, so a new profile
    // works with just an API key. `workspaceId` stays for anyone pointed at a
    // regional `*.maas.aliyuncs.com` host by their own provider page.
    // `voice` is load-bearing even for text-only subtitles: the session the
    // server starts has voice `Chelsie`, which this model rejects on its first
    // turn, and the connection dies before any caption exists.
    // Saving a profile writes these numbers into providers.json, where they win
    // over the backend builtin -- so silenceDurationMs has to agree with config.py.
    "dashscope-livetranslate-realtime": { model: "qwen3.5-livetranslate-flash-realtime", baseUrl: "wss://dashscope.aliyuncs.com/api-ws/v1/realtime", options: { sampleRate: 16000, workspaceId: "", voice: "Tina", audioOutput: false, silenceDurationMs: 800, sourceLanguage: "", nativeTranslationFallback: false, closeDrainTimeoutSeconds: 15 } },
    "dashscope-qwen-realtime": { model: "qwen3-asr-flash-realtime", baseUrl: "wss://dashscope.aliyuncs.com/api-ws/v1/realtime", options: { sampleRate: 16000 } },
    "dashscope-task-asr": { model: "fun-asr-realtime-2026-02-28", baseUrl: "wss://dashscope.aliyuncs.com/api-ws/v1/inference", options: { sampleRate: 16000, heartbeat: true } },
    // No `language` here. The adapter builds its request from the global
    // SourceLanguagePolicy (asr_openai_transcriptions.py:59), so a catalog key
    // was a control that changed nothing -- and seeding it into every new profile
    // kept writing a dead key for the user to find later. A catalog that still
    // carries one loads, round-trips and is ignored, as before.
    "openai-audio-transcriptions": { model: "", baseUrl: "https://api.openai.com/v1", options: { windowSeconds: 3, requestTimeoutSeconds: 20 } },
    "deepgram-streaming": { model: "nova-3", baseUrl: "wss://api.deepgram.com/v1/listen", options: { interimResults: true, smartFormat: true, endpointingMs: 100, vadEvents: true, utteranceEndMs: 1000, keepAliveSeconds: 8 } },
    "soniox-realtime": { model: "stt-rt-v5", baseUrl: "wss://stt-rt.soniox.com/transcribe-websocket", options: { enableEndpointDetection: true, enableLanguageIdentification: true, enableSpeakerDiarization: true, maxEndpointDelayMs: 700, endpointSensitivity: 0.3, translationType: "one_way", nativeTranslationFallback: true, nativeTranslationTimeoutSeconds: 15 } },
    "soniox-realtime-transcribe": { model: "stt-rt-v5", baseUrl: "wss://stt-rt.soniox.com/transcribe-websocket", options: { enableEndpointDetection: true, enableLanguageIdentification: true, enableSpeakerDiarization: true, maxEndpointDelayMs: 700, endpointSensitivity: 0.3 } },
    "openai-realtime-transcription": { model: "gpt-live-transcribe", baseUrl: "wss://api.openai.com/v1/realtime", options: { delay: "low" } },
    "assemblyai-streaming": { model: "universal-3-5-pro", baseUrl: "wss://streaming.assemblyai.com/v3/ws", options: { mode: "balanced", continuousPartials: true, speakerLabels: true, maxSpeakers: 6 } },
    "volcengine-sauc": { model: "bigmodel_async", baseUrl: "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async", options: { resourceId: "volc.bigasr.sauc.concurrent", authMode: "new" } },
    "elevenlabs-scribe-realtime": { model: "scribe_v2_realtime", baseUrl: "wss://api.elevenlabs.io/v1/speech-to-text/realtime", options: { commitStrategy: "vad", vadSilenceThresholdSecs: 0.5, includeLanguageDetection: true } },
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

  /*
   * 每个语音识别协议对应厂商的官网和 API Key 页面。新手最卡的一步是「Key 去哪
   * 拿」，所以链接直接放在 API Key 输入框下面，随协议一起切换。地址在
   * 2026-09 逐个打开核对过；未登录时先跳到登录页是正常的。DashScope 的中国大陆
   * 与国际站是两套账号和两个控制台，所以两条都给。
   */
  const SONIOX_LINKS = { site: "https://soniox.com/", key: "https://console.soniox.com/" };
  const DASHSCOPE_LINKS = {
    site: "https://help.aliyun.com/zh/model-studio/",
    key: "https://bailian.console.aliyun.com/?tab=model#/api-key",
    keyIntl: "https://modelstudio.console.alibabacloud.com/?tab=playground#/api-key",
  };
  const OPENAI_LINKS = { site: "https://developers.openai.com/api/docs/guides/speech-to-text", key: "https://platform.openai.com/api-keys" };
  const providerLinks = {
    "soniox-realtime": SONIOX_LINKS,
    "soniox-realtime-transcribe": SONIOX_LINKS,
    "dashscope-livetranslate-realtime": DASHSCOPE_LINKS,
    "dashscope-qwen-realtime": DASHSCOPE_LINKS,
    "dashscope-task-asr": DASHSCOPE_LINKS,
    "openai-audio-transcriptions": OPENAI_LINKS,
    "openai-realtime-transcription": OPENAI_LINKS,
    "deepgram-streaming": { site: "https://deepgram.com/", key: "https://console.deepgram.com/" },
    "assemblyai-streaming": { site: "https://www.assemblyai.com/", key: "https://www.assemblyai.com/dashboard" },
    "volcengine-sauc": { site: "https://www.volcengine.com/product/voice-tech", key: "https://console.volcengine.com/speech/app" },
    "elevenlabs-scribe-realtime": { site: "https://elevenlabs.io/speech-to-text", key: "https://elevenlabs.io/app/developers/api-keys" },
    "speechmatics-realtime": { site: "https://www.speechmatics.com/", key: "https://portal.speechmatics.com/settings/api-keys" },
    "tencent-asr": { site: "https://cloud.tencent.com/product/asr", key: "https://console.cloud.tencent.com/cam/capi" },
  };

  function providerLinksHtml(kind) {
    const links = providerLinks[kind];
    if (!links) return "";
    const link = (href, text) => `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(text)} ↗</a>`;
    return `<span class="provider-links">${[
      link(links.key, links.keyIntl
        ? updateLabel("dlg.model.getKeyCn", "获取 API Key（中国大陆）")
        : updateLabel("dlg.model.getKey", "获取 API Key")),
      links.keyIntl ? link(links.keyIntl, updateLabel("dlg.model.getKeyIntl", "获取 API Key（国际站）")) : "",
      link(links.site, updateLabel("dlg.model.officialSite", "官网")),
    ].join("")}</span>`;
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
      label: section === "asr" ? updateLabel("dlg.model.newAsr", "新语音识别配置") : updateLabel("dlg.model.newTrans", "新翻译配置"),
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

  /*
   * 「已保存配置」原来把 effective 的 JSON 直接摊在对话框底部。用户要看的只有
   * 几件事：用哪个识别、字幕怎么翻、弹幕用哪个翻译、从什么语言翻到什么语言。
   * 原始 JSON 收进二级折叠，排查问题时再展开。
   */
  function renderEffectiveConfig() {
    const rows = el("effectiveConfigRows");
    if (!rows || !providerCatalog) return;
    const effective = providerCatalog.effective || {};
    el("effectiveConfig").textContent = JSON.stringify(effective, null, 2);
    const nameOf = (section, identity) => {
      if (!identity?.id) return updateLabel("dlg.model.eff.none", "未设置");
      const found = (providerCatalog[section]?.providers || []).find((p) => p.id === identity.id);
      const model = identity.model || found?.model;
      const name = profileText(identity.id, found?.label || model || identity.id);
      return model && !name.includes(model) ? `${name} · ${model}` : name;
    };
    const languageName = (tag) => {
      try {
        return new Intl.DisplayNames([document.documentElement.lang || "zh-CN"], { type: "language" }).of(tag) || tag;
      } catch {
        return tag;
      }
    };
    const subtitle = effective.subtitleTranslation || {};
    const subtitleText = subtitle.mode === "separate-model"
      ? nameOf("translation", subtitle.provider)
      : updateLabel("dlg.model.eff.native", "由语音识别服务直接翻译")
        + (subtitle.fallbackProvider
          ? updateLabel("dlg.model.eff.fallback", "；失败时改用 {name}", { name: nameOf("translation", subtitle.fallbackProvider) })
          : "");
    const chatId = effective.chatTranslation?.active;
    const languages = effective.languages || {};
    const source = languages.sourceLanguage?.mode === "specified" && languages.sourceLanguage.tag
      ? languageName(languages.sourceLanguage.tag)
      : updateLabel("dlg.model.eff.auto", "自动识别");
    const target = languages.targetLanguage ? languageName(languages.targetLanguage) : "—";
    const asrName = nameOf("asr", effective.asr);
    const entries = [
      [updateLabel("dlg.model.eff.asr", "语音识别"), asrName],
      [updateLabel("dlg.model.eff.subtitle", "字幕翻译"), subtitleText],
      [updateLabel("dlg.model.eff.chat", "弹幕翻译"), chatId ? nameOf("translation", { id: chatId }) : updateLabel("dlg.model.eff.none", "未设置")],
      [updateLabel("dlg.model.eff.languages", "语言"), `${source} → ${target}`],
      [updateLabel("dlg.model.eff.workers", "同时翻译"), String(effective.translationWorkers ?? "—")],
    ];
    rows.innerHTML = entries
      .map(([label, value]) => `<div class="saved-config-row"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`)
      .join("");
    if (effective.configurationPath) {
      rows.insertAdjacentHTML("beforeend", `<div class="saved-config-row"><dt>${escapeHtml(updateLabel("dlg.model.eff.path", "配置文件"))}</dt><dd class="mono saved-config-path">${escapeHtml(effective.configurationPath)}</dd></div>`);
    }
    el("effectiveConfigBrief").textContent = asrName;
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
      if (p.id === group.active) uses.push(section === "asr" ? updateLabel("dlg.model.pickedAsr", "识别已选") : updateLabel("dlg.model.pickedSubs", "字幕已选"));
      if (section === "translation" && p.id === providerCatalog.chatTranslation?.active) uses.push(updateLabel("dlg.model.pickedChat", "弹幕已选"));
      button.textContent = `${isRecommended(p) ? "⭐ " : ""}${profileText(p.id, p.label || p.model)}  ${uses.join(" · ")}`;
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
      const kinds = providerKinds[section].map(([group, items]) => {
        // The captions are vendor names plus a qualifier, and four of them carry
        // Chinese that no other locale can read, so each resolves through
        // `proto.<kind>` and keeps this label as the fallback.
        const options = items.map(([value, itemLabel]) =>
          `<option value="${value}"${provider.kind === value ? " selected" : ""}>${escapeHtml(updateLabel(`proto.${value}`, itemLabel))}</option>`
        ).join("");
        const heading = providerKindGroups[group];
        if (!heading) return options;
        return `<optgroup label="${escapeHtml(updateLabel(heading[0], heading[1]))}">${options}</optgroup>`;
      }).join("");
      card.innerHTML = `
        <header class="provider-profile-head">
          <strong class="active-provider">${updateLabel("dlg.model.editing", "正在编辑：")}${escapeHtml(profileText(provider.id, provider.label || provider.model))}</strong>
          ${recommendedBadge(provider)}
          <code>${escapeHtml(provider.id)}</code>
          <button class="secondary compact provider-delete" type="button" data-action="delete" ${cannotDelete ? "disabled" : ""}>${updateLabel("action.delete", "删除")}</button>
        </header>
        <div class="provider-fields">
          <label><span>${updateLabel("field.name", "名称")}</span><input data-field="label" value="${escapeHtml(provider.label || "")}" required></label>
          <label><span>${updateLabel("field.protocol", "协议")}</span><select data-field="kind">${kinds}</select></label>
          <label><span>${updateLabel("field.model", "模型")}</span><input data-field="model" value="${escapeHtml(provider.model || "")}" placeholder="${updateLabel("ph.modelId", "填写厂商模型 ID")}" required></label>
          <label><span>Base URL</span><input data-field="baseUrl" value="${escapeHtml(provider.baseUrl || "")}" required></label>
          <label class="wide"><span>API Key</span><input data-field="apiKey" type="password" value="${escapeHtml(provider.apiKey || "")}" autocomplete="off"><small>${updateLabel("dlg.model.keyLocal", "凭据保存在本机。")}</small>${section === "asr" ? providerLinksHtml(provider.kind) : ""}<button type="button" class="secondary compact" data-action="reveal">${updateLabel("action.showKey", "显示 Key")}</button></label>
          <details class="wide"><summary>${updateLabel("dlg.model.advanced", "价格与高级设置")}</summary><div class="provider-fields">${section === "asr" ? asrPricingFields(provider) : translationPricingFields(provider)}
          ${section === "asr" ? asrOptionFields(provider) : translationOptionFields(provider)}</div></details>
        </div>`;
      card.querySelector('[data-action="reveal"]').addEventListener("click", (event) => {
        const input = card.querySelector('[data-field="apiKey"]');
        input.type = input.type === "password" ? "text" : "password";
        event.target.textContent = input.type === "password"
          ? updateLabel("action.showKey", "显示 Key")
          : updateLabel("action.hideKey", "隐藏 Key");
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

  /*
   * 弹窗里这些字段是 JS 拼出来的，进不了 BINDINGS 的静态绑定，所以只能在这里
   * 直接问 i18n。第二个参数是中文兜底：键还没补上时退回现在的文案，不会因为
   * 漏翻译而显示成空字符串或键名。
   */
  function asrPricingFields(provider) {
    const currency = provider.currency || "CNY";
    const notEstimable = updateLabel("ph.notEstimable", "留空表示不可估算");
    return `<label class="wide"><span>${updateLabel("price.asr", "ASR 单价（{currency} / 秒）", { currency })}</span><input data-field="pricePerSecondCny" type="number" min="0" step="any" value="${pricingValue(provider.pricePerSecondCny)}" placeholder="${notEstimable}"><small>${updateLabel("price.asr.hint", "本地免费服务请显式填写 0；留空不是免费。")}</small></label>`;
  }

  function translationPricingFields(provider) {
    const currency = provider.currency || "CNY";
    return `
      <label><span>${updateLabel("price.input", "普通输入（{currency} / 百万 token）", { currency })}</span><input data-field="pricePerMillionInputTokensCny" type="number" min="0" step="any" value="${pricingValue(provider.pricePerMillionInputTokensCny)}" placeholder="${updateLabel("ph.notEstimable", "留空不可估算")}"></label>
      <label><span>${updateLabel("price.cachedInput", "缓存输入（{currency} / 百万 token）", { currency })}</span><input data-field="pricePerMillionCachedInputTokensCny" type="number" min="0" step="any" value="${pricingValue(provider.pricePerMillionCachedInputTokensCny)}" placeholder="${updateLabel("ph.notEstimable", "留空不可估算")}"></label>
      <label><span>${updateLabel("price.cacheWrite", "缓存写入（{currency} / 百万 token）", { currency })}</span><input data-field="pricePerMillionCacheWriteTokensCny" type="number" min="0" step="any" value="${pricingValue(provider.pricePerMillionCacheWriteTokensCny)}" placeholder="${updateLabel("ph.cacheWriteOptional", "可选；无缓存写入可留空")}"></label>
      <label><span>${updateLabel("price.output", "输出（{currency} / 百万 token）", { currency })}</span><input data-field="pricePerMillionOutputTokensCny" type="number" min="0" step="any" value="${pricingValue(provider.pricePerMillionOutputTokensCny)}" placeholder="${updateLabel("ph.notEstimable", "留空不可估算")}"></label>`;
  }

  function checked(value) {
    return value ? " checked" : "";
  }

  function translationModeField(options, L) {
    const mode = options.translationType === "one_way" || options.translationType === "two_way"
      ? options.translationType
      : "";
    return `
      <label><span>${L("opt.translationMode", "Provider 内置翻译")}</span><select data-option="translationType">
        <option value=""${mode === "" ? " selected" : ""}>${L("opt.translationOff", "关闭（只做识别）")}</option>
        <option value="one_way"${mode === "one_way" ? " selected" : ""}>${L("opt.translationOneWay", "单向（识别＋译成目标语言，出双语字幕）")}</option>
        <option value="two_way"${mode === "two_way" ? " selected" : ""}>${L("opt.translationTwoWay", "双向（语言 A ↔ 语言 B 互译，需填两侧）")}</option>
      </select></label>
      <p class="provider-hint wide">${L("opt.translationHelp", "这里控制 Soniox 是否在同一会话里翻译：关闭=只识别；单向=源语言→字幕目标语言；双向=语言 A↔语言 B，适合双语对话。单向和双向都会送回原文与译文两条文本，画面上显示几行由“字幕显示→显示”决定（选了“仅译文”就只剩一行译文）。")}</p>
      <label class="wide"><span><input data-option="nativeTranslationFallback" type="checkbox"${checked(options.nativeTranslationFallback)}> ${L("opt.nativeTranslationFallback", "原生译文缺失或无法对齐时，调用字幕翻译模型（额外费用）")}</span></label>
      <label><span>${L("opt.translationLanguageA", "双向语言 A（对话用）")}</span><input data-option="translationLanguageA" value="${escapeHtml(options.translationLanguageA || "")}" placeholder="ja"></label>
      <label><span>${L("opt.translationLanguageB", "双向语言 B（对话用）")}</span><input data-option="translationLanguageB" value="${escapeHtml(options.translationLanguageB || "")}" placeholder="zh"></label>`;
  }

  function asrOptionFields(provider) {
    const options = provider.options || {};
    const L = (key, fallback) => updateLabel(key, fallback);
    if (provider.kind === "dashscope-livetranslate-realtime") return `
      <label class="wide"><span><input data-option="nativeTranslationFallback" type="checkbox"${checked(options.nativeTranslationFallback)}> ${L("opt.nativeTranslationFallback", "原生译文缺失或无法对齐时，调用字幕翻译模型（额外费用）")}</span></label>
      ${String(provider.model).startsWith("qwen3.8") ? `<p class="provider-hint wide">${L("opt.qwen38TimingWarning", "3.8 双语原文在实测中出现跨段错位；15 秒缓冲不能修复错误归属。字幕时序优先时请使用 3.5 配置。")}</p>` : ""}
      <label><span><input data-option="audioOutput" type="checkbox"${checked(options.audioOutput)}> ${L("opt.audioOutput", "同时输出合成语音（额外计费）")}</span></label>
      ${options.audioOutput ? `<label><span>${L("opt.livetranslateVoice", "翻译语音的音色（voice）")}</span><input data-option="voice" value="${escapeHtml(options.voice || "Tina")}" placeholder="Tina"><small>${L("opt.livetranslateVoiceHelp", "听到的译文语音用什么声音，不填默认 Tina。")}</small></label>` : ""}
      <label><span>${L("opt.pinSourceLanguage", "锁定源语言（留空=自动识别）")}</span><input data-option="sourceLanguage" value="${escapeHtml(options.sourceLanguage || "")}" placeholder="ja"></label>
      <label><span>${L("opt.silenceDurationMs", "断句静音阈值 ms")}</span><input data-option="silenceDurationMs" type="number" min="200" max="6000" step="100" value="${Number(options.silenceDurationMs || (String(provider.model).startsWith("qwen3.5") ? 800 : 300))}"><small>${L("opt.silenceDurationMsHelp", "静音阈值控制断句，不校正字幕时间戳。过短会切碎话语；3.5 字幕配置使用 800 ms，长句由本地上限处理。")}</small></label>
      <label><span>${L("opt.workspaceId", "业务空间 ID（可选）")}</span><input data-option="workspaceId" value="${escapeHtml(options.workspaceId || "")}" placeholder="llm-xxxxxxxx"><small>${L("opt.workspaceIdHelp", "默认的全局地址不需要它。只有把 Base URL 换成 wss://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/… 这类按业务空间分配的地址时才填，填了会自动替换地址里的占位符。")}</small></label>
      <label><span>${L("opt.nativeTimeout", "内置翻译等待（秒）")}</span><input data-option="nativeTranslationTimeoutSeconds" type="number" min="3" max="60" step="1" value="${Number(options.nativeTranslationTimeoutSeconds || 15)}"></label>
      <p class="provider-hint wide">${L("opt.livetranslateHint", "这个 Profile 是字幕接口：默认只往画面上送原文和译文，不合成语音（实测一场直播下来一个语音字节都没收到）。译文语言取字幕设置里的目标语言，源语言由模型自己判断；锁定源语言只对 3.5 代模型生效，3.8 没有这个参数，始终自动识别。")}</p>`;
    if (provider.kind === "openai-audio-transcriptions") return `
      <label><span>${L("opt.windowSeconds", "分窗秒数")}</span><input data-option="windowSeconds" type="number" min="0.5" step="0.5" value="${Number(options.windowSeconds || 3)}"></label>
      <label><span>${L("opt.requestTimeoutSeconds", "请求超时（秒）")}</span><input data-option="requestTimeoutSeconds" type="number" min="1" step="1" value="${Number(options.requestTimeoutSeconds || 20)}"></label>`;
    if (provider.kind === "soniox-realtime" || provider.kind === "soniox-realtime-transcribe") {
      // The transcribe-only entry shares the model and every control with the
      // bilingual one, minus the switch: the backend pins translating off for it,
      // so offering a control that cannot take effect would be a lie.
      const transcribeOnly = provider.kind === "soniox-realtime-transcribe";
      return `
      <label><span><input data-option="enableEndpointDetection" type="checkbox"${checked(options.enableEndpointDetection !== false)}> ${L("opt.endpointDetection", "端点检测")}</span></label>
      <label><span><input data-option="enableLanguageIdentification" type="checkbox"${checked(options.enableLanguageIdentification !== false)}> ${L("opt.languageIdentification", "语言识别")}</span></label>
      <label><span><input data-option="enableSpeakerDiarization" type="checkbox"${checked(options.enableSpeakerDiarization)}> ${L("opt.speakerDiarization", "说话人分离")}</span></label>
      <label><span>${L("opt.maxEndpointDelayMs", "最大端点延迟 ms")}</span><input data-option="maxEndpointDelayMs" type="number" min="500" max="3000" step="100" value="${Number(options.maxEndpointDelayMs || 700)}"></label>
      ${transcribeOnly
        ? `<p class="provider-hint wide">${L("opt.transcribeOnlyHint", "这条协议只做识别，不会在会话里翻译；译文由「字幕翻译」那一个模型来出。")}</p>`
        : translationModeField(options, L)}`;
    }
    if (provider.kind === "deepgram-streaming") return `
      <label><span><input data-option="diarize" type="checkbox"${checked(options.diarize)}> ${L("opt.diarizeBilled", "说话人分离（附加计费）")}</span></label>
      <label><span>${L("opt.endpointingMs", "Endpointing ms")}</span><input data-option="endpointingMs" type="number" min="1" step="10" value="${Number(options.endpointingMs || 300)}"></label>
      <label><span>${L("opt.utteranceEndMs", "Utterance End ms")}</span><input data-option="utteranceEndMs" type="number" min="0" step="100" value="${Number(options.utteranceEndMs || 0)}"></label>`;
    if (provider.kind === "assemblyai-streaming") return `
      <label><span>${L("opt.mode", "模式")}</span><select data-option="mode"><option value="balanced"${options.mode === "balanced" ? " selected" : ""}>balanced</option><option value="min_latency"${options.mode === "min_latency" ? " selected" : ""}>min_latency</option><option value="max_accuracy"${options.mode === "max_accuracy" ? " selected" : ""}>max_accuracy</option></select></label>
      <label><span><input data-option="speakerLabels" type="checkbox"${checked(options.speakerLabels)}> ${L("opt.speakerDiarization", "说话人分离")}</span></label>
      <label><span>${L("opt.maxSpeakers", "最多说话人")}</span><input data-option="maxSpeakers" type="number" min="1" max="10" step="1" value="${Number(options.maxSpeakers || 6)}"></label>`;
    if (provider.kind === "volcengine-sauc") return `
      <label><span>Resource ID</span><input data-option="resourceId" value="${escapeHtml(options.resourceId || "volc.bigasr.sauc.concurrent")}"></label>
      <label><span>${L("opt.appKey", "旧控制台 App Key")}</span><input data-option="appKey" value="${escapeHtml(options.appKey || "")}"></label>
      <label><span>${L("opt.authMode", "鉴权模式")}</span><select data-option="authMode"><option value="new"${options.authMode !== "legacy" ? " selected" : ""}>${L("opt.consoleNew", "新控制台")}</option><option value="legacy"${options.authMode === "legacy" ? " selected" : ""}>${L("opt.consoleLegacy", "旧控制台")}</option></select></label>`;
    if (provider.kind === "elevenlabs-scribe-realtime") return `
      <label><span>${L("opt.commitStrategy", "提交策略")}</span><select data-option="commitStrategy"><option value="manual"${options.commitStrategy !== "vad" ? " selected" : ""}>manual</option><option value="vad"${options.commitStrategy === "vad" ? " selected" : ""}>vad</option></select></label>
      <label><span><input data-option="includeTimestamps" type="checkbox"${checked(options.includeTimestamps)}> ${L("opt.includeTimestamps", "延迟词时间戳")}</span></label>
      <label><span><input data-option="includeLanguageDetection" type="checkbox"${checked(options.includeLanguageDetection)}> ${L("opt.includeLanguageDetection", "返回检测语言")}</span></label>`;
    if (provider.kind === "speechmatics-realtime") return `
      <label><span><input data-option="enablePartials" type="checkbox"${checked(options.enablePartials !== false)}> ${L("opt.enablePartials", "中间结果")}</span></label>
      <label><span><input data-option="diarization" type="checkbox"${checked(options.diarization)}> ${L("opt.speakerDiarization", "说话人分离")}</span></label>
      <label><span>${L("opt.maxSpeakers", "最多说话人")}</span><input data-option="maxSpeakers" type="number" min="2" step="1" value="${Number(options.maxSpeakers || 6)}"></label>
      <label><span>${L("opt.maxDelaySeconds", "最大延迟秒数")}</span><input data-option="maxDelaySeconds" type="number" min="0.7" max="4" step="0.1" value="${Number(options.maxDelaySeconds || 4)}"></label>`;
    if (provider.kind === "tencent-asr") return `
      <label><span>App ID</span><input data-option="appId" value="${escapeHtml(options.appId || "")}" required></label>
      <label><span>Secret ID</span><input data-option="secretId" value="${escapeHtml(options.secretId || "")}" required></label>
      <label><span>${L("opt.engineModelType", "引擎")}</span><input data-option="engineModelType" value="${escapeHtml(options.engineModelType || provider.model || "16k_ja")}"></label>
      <label><span>Word Info</span><input data-option="wordInfo" type="number" min="0" max="2" step="1" value="${Number(options.wordInfo || 0)}"></label>`;
    if (provider.kind === "dashscope-task-asr") return `
      <label><span>Vocabulary ID</span><input data-option="vocabularyId" value="${escapeHtml(options.vocabularyId || "")}"></label>
      <label><span><input data-option="heartbeat" type="checkbox"${checked(options.heartbeat)}> ${L("opt.heartbeat", "静音保活")}</span></label>`;
    return "";
  }

  function translationOptionFields(provider) {
    const options = provider.options || {};
    const L = (key, fallback) => updateLabel(key, fallback);
    return `
      <label><span>${L("opt.temperature", "温度")}</span><input data-option="temperature" type="number" min="0" max="2" step="0.1" value="${Number(options.temperature ?? 0.3)}"></label>
      <label><span>${L("opt.maxTokens", "最大 Tokens")}</span><input data-option="maxTokens" type="number" min="32" step="1" value="${Number(options.maxTokens || 256)}"></label>
      <label><span>${L("opt.timeoutSeconds", "超时（秒）")}</span><input data-option="timeoutSeconds" type="number" min="1" step="1" value="${Number(options.timeoutSeconds || 6)}"></label>
      <label><span>${L("opt.contextPairs", "字幕上下文对数（弹幕不使用）")}</span><input data-option="contextPairs" type="number" min="0" step="1" value="${Number(options.contextPairs ?? 6)}"></label>`;
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
      el("modelSettingsFeedback").textContent = updateLabel("dlg.model.protocolApplied", "已应用该协议的默认模型与地址（名称和 API Key 保持不变）。");
    }
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  }

  function closeModelSettings() {
    el("modelSettingsDialog").close();
  }

  const cookieDrafts = new Map();
  let cookieDraftPlatform = el("cookiePlatform")?.value || null;

  function updateCookiePlatformHelp() {
    const platformSelect = el("cookiePlatform");
    const payloadField = el("cookiePayload");
    const platform = platformSelect.value;
    // Keep only unsent text in memory, keyed by platform. A platform change
    // must never leave a YouTube Cookie visible while Bilibili is selected,
    // and saved Cookie values are intentionally never fetched back into this
    // field from disk.
    if (cookieDraftPlatform === null) {
      cookieDraftPlatform = platform;
    } else if (cookieDraftPlatform !== platform) {
      cookieDrafts.set(cookieDraftPlatform, payloadField.value);
      payloadField.value = cookieDrafts.get(platform) || "";
      cookieDraftPlatform = platform;
    }
    const bilibili = platform === "bilibili";
    const twitch = platform === "twitch";
    el("cookieImportIntro").textContent = bilibili
      ? updateLabel("dlg.cookie.intro.bilibili", "B 站：想看登录后才开放的高画质时需要，关键的一项是 SESSDATA。请在 bilibili.com 页面上导出。")
      : twitch
        ? updateLabel("dlg.cookie.intro.twitch", "Twitch：公开直播完全不需要 Cookie，只有订阅者专享等受限直播才需要。请在 twitch.tv 页面上导出。")
        : updateLabel("dlg.cookie.intro.youtube", "YouTube：遇到“登录以确认你不是机器人”、会员专享或年龄限制时需要。请在 youtube.com 页面上导出。");
    payloadField.placeholder = updateLabel("dlg.cookie.ph", "把 cookies.txt 的全部内容粘贴到这里\n（也支持开发者工具里复制的名称/值行）");
  }

  /*
   * 教程推荐的是插件导出的 cookies.txt，而格式下拉默认是「名称/值 多行」。
   * 粘贴的内容一看就是 Netscape 文件（文件头，或每行 7 个制表符分隔的字段）
   * 时替用户切过去，免得按教程做完还要自己找这个下拉框。
   */
  function detectCookieFormat() {
    const text = el("cookiePayload").value;
    const netscape = /^#\s*(?:Netscape\s+)?HTTP Cookie File/im.test(text)
      || /^[^\s#][^\t\r\n]*(?:\t[^\t\r\n]*){6}$/m.test(text);
    if (netscape) el("cookieFormat").value = "netscape";
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
    feedback.textContent = updateLabel("dlg.cookie.importing", "正在导入…");
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
          ? updateLabel("dlg.cookie.missingSessdata", "已保存 {count} 个 Bilibili Cookie，但缺少 yt-dlp 登录关键字段 SESSDATA。请从 bilibili.com 的 Cookie 列表重新复制。", { count: data.accepted })
          : updateLabel("dlg.cookie.missingYoutube", "已保存 {count} 个 YouTube Cookie，但缺少登录关键字段：{fields}。请从 YouTube/Google 登录域的 Cookie 列表重新复制。", { count: data.accepted, fields: data.missingCritical.join("、") });
        feedback.dataset.tone = "error";
      } else {
        const platformName = data.platform === "bilibili" ? "Bilibili" : data.platform === "twitch" ? "Twitch" : "YouTube";
        feedback.textContent = updateLabel(
          data.persisted ? "dlg.cookie.importedSaved" : "dlg.cookie.importedMemory",
          data.persisted
            ? `已导入 ${data.accepted} 个 ${platformName} Cookie（${data.names.join("、")}），已按平台保存到本机。现在可以解析对应平台地址。`
            : `已导入 ${data.accepted} 个 ${platformName} Cookie（${data.names.join("、")}），磁盘保存失败，仅本次运行有效。现在可以解析对应平台地址。`,
          { count: data.accepted, platform: platformName, names: data.names.join("、") },
        );
        feedback.dataset.tone = "success";
        cookieDrafts.set(payload.platform, "");
        el("cookiePayload").value = "";
      }
      el("message").textContent = updateLabel("dlg.cookie.importedNotice", "登录 Cookie 已导入，解析与播放将使用该登录态。");
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
    feedback.textContent = updateLabel("dlg.model.saving", "正在保存…");
    feedback.dataset.tone = "";
    try {
      const payload = structuredClone(providerCatalog);
      const data = await request("/api/model-settings", payload);
      providerCatalog = data;
      renderRoleSelectors(data);
      renderProviderProfiles();
      feedback.textContent = updateLabel("dlg.model.savedNext", "已保存。下次启动直播时使用新配置。");
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
      chip.textContent = `${index === 0 ? updateLabel("section.primary", "首选") + " " : ""}${parts.autonym} · ${tag} ×`;
      chip.setAttribute("aria-label", updateLabel("ph.removeCandidate", "移除候选语言 {tag}", { tag: parts.primary }));
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
    renderRoleModeHint();
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
        if (!tagCoveredBy(policy.tag, asr.supportedTags)) return updateLabel("hint.sourceTagUnsupported", "当前 ASR 不支持源语言 {tag}", { tag: policy.tag });
      } else {
        if (asr.detection === "none") return updateLabel("hint.detectionUnsupported", "当前 ASR 不支持自动识别源语言，请指定语言");
        if (asr.detection === "candidates" && !policy.candidates.length) return updateLabel("hint.needCandidates", "当前 ASR 自动识别需要候选语言");
        if (asr.maxCandidates != null && policy.candidates.length > asr.maxCandidates) return updateLabel("hint.maxCandidates", "当前 ASR 自动识别最多支持 {max} 个候选语言", { max: asr.maxCandidates });
        const candidateScope = asr.detectionTags ?? asr.supportedTags;
        const unsupported = policy.candidates.filter((tag) => !tagCoveredBy(tag, candidateScope));
        if (unsupported.length) return updateLabel("hint.candidatesUnsupported", "当前 ASR 不支持候选语言：{tags}", { tags: unsupported.join("、") });
        if (policy.allowCodeSwitching && !asr.codeSwitching) return updateLabel("hint.noCodeSwitching", "当前 ASR 不支持混合语言（code-switching）识别");
      }
    }
    const translation = languageState.translation?.language;
    const target = targetSelector?.value;
    if (translation && target && !translation.openWorldPrompting && !tagCoveredBy(target, translation.targetTags)) {
      return updateLabel("hint.translationTargetUnsupported", "当前翻译 Provider 不支持目标语言 {target}", { target });
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
        el("message").textContent = updateLabel("msg.targetLanguageSwitched", "目标语言已切换为 {tag}；后续字幕立即使用新语言。", { tag: parts.primary });
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
        placeholder: updateLabel("ph.addCandidate", "添加候选语言…"),
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
    if (roleCatalog) renderRoleSelectors(roleCatalog);
    renderRoleModeHint();
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
      renderRoleModeHint();
    } catch (error) {
      showError(error);
    }
  }

  // Recommendations use matched cue arrivals and the actual viewer position.
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

  function resetSubtitleBudget() {
    subtitleReadiness.clear();
    subtitleBudget.lowSince = null;
    subtitleBudget.suggested = null;
    subtitleBudget.primed = false;
    subtitleBudget.checkedAt = null;
    el("budgetMargin").textContent = "—";
    el("applyDelayButton").hidden = true;
  }

  function subtitleBudgetContext() {
    const target = Number(el("targetDelay").value);
    const offset = subtitlePrefs.offset;
    const key = `${target}:${offset}`;
    if (subtitleBudget.context !== undefined && subtitleBudget.context !== key) resetSubtitleBudget();
    subtitleBudget.context = key;
    const valid = lastSessionState === "running" && subtitlePrefs.enabled
      && !video.paused && !video.seeking && !document.hidden
      && video.readyState >= 3 && video.playbackRate === 1;
    if (subtitleBudget.active !== valid && (!valid || subtitleBudget.active === false)) resetSubtitleBudget();
    subtitleBudget.active = valid;
    return valid;
  }

  function subtitleLeadStats() {
    const now = performance.now();
    const samples = [...subtitleReadiness.values()].map(sample => sample.translation)
      .filter(sample => sample?.eligible && !sample.paused && !sample.seeking && !sample.hidden
        && sample.playbackRate === 1 && sample.offset === subtitlePrefs.offset
        && sample.target === Number(el("targetDelay").value)
        && Number.isFinite(sample.leadSeconds) && now >= sample.monotonic && now - sample.monotonic <= 60000);
    if (samples.length < 5) return null;
    const latest = Math.max(...samples.map(sample => sample.monotonic));
    if (now - latest > 10000) return null;
    const leads = samples.map(sample => sample.leadSeconds).sort((a, b) => a - b);
    // Lower 5th percentile of MATCHED observations, not the subtraction of
    // independent duration/processing percentiles. Positive means arrived early.
    return { margin: leads[Math.floor((leads.length - 1) * 0.05)], latest, count: leads.length };
  }

  function updateSubtitleBudget(subtitles, totalDelaySeconds, targetDelaySeconds) {
    const pipelineContext = JSON.stringify([subtitles.asrProviderId, subtitles.translationProviderId, subtitles.targetLanguage]);
    if (subtitleBudget.pipelineContext !== undefined && subtitleBudget.pipelineContext !== pipelineContext) resetSubtitleBudget();
    subtitleBudget.pipelineContext = pipelineContext;
    // Terminal outcomes include failures; only successful translations tell
    // us how long a translated caption actually took to become available.
    const p50 = subtitles.translationProcessingP50;
    const p95 = subtitles.translationProcessingP95;
    const hasStats = Number.isFinite(p95);

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
      ? `${scheduler.droppedLateCues} ${updateLabel("cost.dropped", "丢")} / ${scheduler.lateCues} ${updateLabel("cost.late", "迟到")}`
      : "—";

    const spans = cueDurationPercentiles();
    el("cueDuration").textContent = spans ? `${spans.p50.toFixed(1)}s / ${spans.p95.toFixed(1)}s` : "—";

    el("subtitleReadyLag").textContent = hasStats
      ? `${Number.isFinite(p50) ? p50.toFixed(2) : "—"}s / ${p95.toFixed(2)}s`
      : "—";
    const valid = subtitleBudgetContext();
    const leads = valid ? subtitleLeadStats() : null;
    const now = performance.now();
    if (Number.isFinite(subtitleBudget.checkedAt) && now - subtitleBudget.checkedAt > 10000) subtitleBudget.lowSince = null;
    subtitleBudget.checkedAt = now;
    el("budgetMargin").textContent = leads ? seconds(leads.margin) : "—";
    subtitleBudget.suggested = null;
    el("applyDelayButton").hidden = true;
    if (!leads || leads.margin >= 0.5) {
      subtitleBudget.lowSince = null;
      return;
    }
    if (subtitleBudget.lowSince === null) subtitleBudget.lowSince = now;
    // A frozen sample cannot keep a recommendation alive or advance its timer.
    if (leads.latest - subtitleBudget.lowSince < 30000) return;
    const current = Number(targetDelaySeconds);
    if (!Number.isFinite(current) || current < 11 || current >= 60) return;
    const needed = Math.min(60, current + Math.max(1, Math.ceil(1.5 - leads.margin)));
    subtitleBudget.suggested = needed;
    el("applyDelayButton").hidden = false;
    el("applyDelayButton").textContent = updateLabel("msg.raiseDelay", `将目标延迟调到 ${needed} 秒`, { n: needed });
  }

  async function applySuggestedDelay() {
    const suggested = subtitleBudget.suggested;
    const leads = subtitleBudgetContext() ? subtitleLeadStats() : null;
    if (!Number.isFinite(suggested) || !leads || leads.margin >= 0.5
        || !Number.isFinite(subtitleBudget.checkedAt) || performance.now() - subtitleBudget.checkedAt > 10000
        || suggested <= Number(el("targetDelay").value)) return;
    const claim = uiGeneration;
    const button = el("applyDelayButton");
    button.disabled = true;
    try {
      await request("/api/target-delay", { seconds: suggested });
      if (claim !== uiGeneration) return;
      el("targetDelay").value = String(suggested);
      localStorage.setItem("lingerlens.targetDelaySeconds", String(suggested));
      el("message").textContent = updateLabel("msg.delayApplied", `目标延迟已调到 ${suggested} 秒，将继续观察字幕是否及时。`, { n: suggested });
      resetSubtitleBudget();
      button.hidden = true;
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  }

  async function refreshSubtitles() {
    if (!subtitlePrefs.enabled) return;
    const claim = uiGeneration;
    try {
      // 单调序列号游标（redesign Fix I）：时钟绝不参与轮询进度。
      const playhead = playingWallClock();
      const position = Number.isFinite(playhead) && observedMediaSessionId !== null
        ? `&playhead=${playhead.toFixed(3)}&mediaSessionId=${encodeURIComponent(observedMediaSessionId)}` : "";
      const data = await request(`/api/subtitles?afterSeq=${subtitleAfterSeq}${position}`);
      if (claim !== uiGeneration) return;
      if ("mediaSessionId" in data && data.mediaSessionId !== observedMediaSessionId) return;
      const observing = subtitleBudgetContext();
      for (const cue of data.cues || []) {
        const merge = window.mergeSubtitleCueBySeq || ((cues, incoming) => {
          const current = cues.get(incoming.id);
          if (!current || Number(incoming.seq) > Number(current.seq)) cues.set(incoming.id, incoming);
        });
        const before = subtitleCues.get(cue.id);
        merge(subtitleCues, cue);
        const after = subtitleCues.get(cue.id);
        if (after !== before) subtitleCueOrderDirty = true;
        if (subtitleCues.get(cue.id) === cue) recordSubtitleReadiness(cue);
      }
      subtitleBudget.primed = observing;
      // Recognized but not yet a cue. Held verbatim and checked at paint time:
      // a payload that cannot be placed on the timeline is no line at all, and
      // the next poll replaces it, so a dropped poll drops the line too.
      subtitleDraft = Array.isArray(data.drafts) ? data.drafts : data.draft || null;
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
        if (cue.tEnd + cue.hold < cutoff) {
          subtitleCues.delete(id);
          subtitleCueOrderDirty = true;
        }
      }
    } catch (error) {
      if (claim !== uiGeneration) return;
      subtitleDraft = null;
      resetSubtitleBudget();
      if (!String(error.message).includes("Failed to fetch")) console.warn("subtitle poll failed", error);
    }
  }

  function playingWallClock() {
    if (Number.isFinite(window.__lingerlensSubtitleTestWallTime)) return window.__lingerlensSubtitleTestWallTime;
    return mediaClock?.playingWallTime() ?? null;
  }

  function recordSubtitleReadiness(cue) {
    const eligible = subtitleBudgetContext() && subtitleBudget.primed;
    let sample = subtitleReadiness.get(cue.id);
    if (!sample) {
      sample = { id: cue.id, generation: cue.generation ?? null, tStart: cue.tStart,
        source: null, translation: null };
      subtitleReadiness.set(cue.id, sample);
    }
    const wall = playingWallClock();
    const observation = {
      at: Date.now() / 1000,
      playhead: Number.isFinite(wall) ? wall : null,
      monotonic: performance.now(), eligible,
      offset: subtitlePrefs.offset, target: Number(el("targetDelay").value),
      leadSeconds: Number.isFinite(wall) && Number.isFinite(cue.tStart) ? cue.tStart - (wall + subtitlePrefs.offset) : null,
      paused: video.paused, seeking: video.seeking, hidden: document.hidden,
      playbackRate: video.playbackRate,
    };
    // Arrival is measured at the actual viewer position. Keep paused/hidden
    // observations labelled so they cannot masquerade as live on-time samples.
    if (cue.src && sample.source === null) sample.source = observation;
    if (cue.state === "done" && cue.zh?.trim() && sample.translation === null) sample.translation = observation;
    while (subtitleReadiness.size > 512) subtitleReadiness.delete(subtitleReadiness.keys().next().value);
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
    if (subtitleCueOrderDirty) {
      sortedSubtitleCues = [...subtitleCues.values()].sort((a, b) => a.tStart - b.tStart);
      subtitleCueOrderDirty = false;
    }
    const sortedCues = sortedSubtitleCues.filter((cue) => subtitleLines(cue) && cue.tStart <= t);

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

    const activeCue = subtitleScheduler ? subtitleScheduler.pick(sortedSubtitleCues, t) : null;
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
      const lines = subtitleLines(cue);
      if (!row) {
        row = document.createElement("div");
        row.className = "timeline-row subtitle-timeline-row";
        row.dataset.cueId = cue.id;
        row.innerHTML = `
          <div class="timeline-time"></div>
          <div class="timeline-body">
            <div class="timeline-text-translated"></div>
            <div class="timeline-text-source"></div>
          </div>
        `;
        row.tabIndex = 0;
        const seekCue = () => seekToWallTime(Number(row.dataset.seekWallTime), row);
        row.addEventListener("click", seekCue);
        row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); seekCue(); } });
        container.appendChild(row);
        hasNewAppended = true;
      }
      // The body is rewritten whenever the cue's revision changes, so a row created
      // from source text is upgraded in place when its translation lands instead of
      // keeping the source for the rest of the session.
      const bodyRevision = `${cue.seq}\u001f${cue.revision}\u001f${lines.translated}\u001f${lines.source}`;
      if (row.dataset.bodyRevision !== bodyRevision) {
        const translatedLine = row.querySelector(".timeline-text-translated");
        const sourceLine = row.querySelector(".timeline-text-source");
        translatedLine.textContent = lines.translated;
        sourceLine.textContent = lines.source;
        translatedLine.hidden = !lines.translated;
        sourceLine.hidden = !lines.source;
        row.dataset.bodyRevision = bodyRevision;
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
      row?.setAttribute("title", updateLabel("player.noDvr", "已超出本地回看窗口"));
      return;
    }
    video.currentTime = position;
  }

  function updatePlayerControls() {
    // Replacing identical text still mutates the DOM and can emit native
    // accessibility events. Keep the 250ms clock, but only publish changes.
    const text = (id, value) => {
      const node = el(id);
      if (node.textContent !== value) node.textContent = value;
    };
    const attribute = (id, name, value) => {
      const node = el(id);
      if (node.getAttribute(name) !== value) node.setAttribute(name, value);
    };
    const range = mediaClock?.seekableWallClockRange();
    const rail = el("seekRail");
    const playLabel = video.paused
      ? updateLabel("player.play", "播放")
      : updateLabel("player.pause", "暂停");
    const muteLabel = video.muted
      ? updateLabel("player.unmute", "取消静音")
      : updateLabel("player.mute", "静音");
    attribute("playPause", "aria-label", playLabel);
    attribute("playPause", "title", playLabel);
    attribute("muteToggle", "aria-label", muteLabel);
    attribute("muteToggle", "title", muteLabel);
    stage.classList.toggle("is-paused", video.paused);
    stage.classList.toggle("is-muted", video.muted);
    text("currentWallTime", mediaClock?.formatTime(range?.currentWallTime) || "--:--:--");
    if (!range || !rail) return;
    if (rail.min !== String(range.startPosition)) rail.min = String(range.startPosition);
    if (rail.max !== String(range.endPosition)) rail.max = String(range.endPosition);
    if (!rail.matches(":active")) {
      const value = String(Math.min(range.endPosition, Math.max(range.startPosition, video.currentTime)));
      if (rail.value !== value) rail.value = value;
    }
    text("seekStartWallTime", mediaClock.formatTime(range.startWallTime));
    text("seekEndWallTime", mediaClock.formatTime(range.endWallTime));
    if (!rail.dataset.dragging) text("seekPreview", mediaClock.formatTime(range.currentWallTime));
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
    const label = fullscreen
      ? updateLabel("player.exitFullscreen", "退出全屏")
      : updateLabel("player.enterFullscreen", "进入全屏");
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
      if (stats.pendingClock) problems.push(`${updateLabel("cost.pendingClock", "等待媒体时钟: ")}${stats.pendingClock}`);
      if (stats.received !== undefined) routine.push(`收到: ${stats.received}`);
      if (stats.translated !== undefined) routine.push(`翻译: ${stats.translated}`);
      if (stats.translationFailed) problems.push(`${updateLabel("cost.failedCount", "失败: ")}${stats.translationFailed}`);
      const failureLabels = { timeout: ["fail.timeout", "翻译超时"], deadline: ["fail.deadline", "翻译超时未完成"], empty: ["fail.empty", "空译文"], json_format: ["fail.json", "JSON格式错误"], batch_count: ["fail.batchCount", "批次数量不符"], batch_item: ["fail.batchItem", "批次内容或编号异常"], batch_ids: ["fail.batchIds", "批次编号不符"], response_format: ["fail.responseShape", "响应格式异常"], rate_limit: ["fail.rateLimit", "翻译限流"], authentication: ["fail.auth", "翻译认证失败"], provider_error: ["fail.provider", "翻译调用失败"] };
      if (stats.translationLastFailure) {
        const labelled = failureLabels[stats.translationLastFailure];
        problems.push(labelled
          ? updateLabel(labelled[0], labelled[1])
          : updateLabel("cost.translationFailed", "翻译失败"));
      }
      if (chatOverlay.getStats().dropped) routine.push(`${updateLabel("cost.chatSkipped", "画面省略: ")}${chatOverlay.getStats().dropped}`);
      if (stats.translationSkipped) routine.push(`${updateLabel("cost.skipped", "跳过: ")}${stats.translationSkipped}`);
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
      const state = stats.translationState === "degraded" ? updateLabel("cost.degraded", " · 降级") : "";
      target.textContent = `${updateLabel("cost.targetLanguage", "目标语言")} ${stats.targetLanguage || targetSelector?.value || "—"}${state}`;
    }
  }

  function renderTimelines() {
    const measure = (name, run) => typeof playbackProbe !== "undefined" && playbackProbe
      ? playbackProbe.measure(name, run) : run();
    const wall = playingWallClock();
    // Both views consume the same immutable snapshot for this render pass.
    // Spreading the store separately made every 333ms tick copy the retained
    // message set twice before either view could do useful work.
    const liveMessages = liveMessagesClient ? [...liveMessagesClient.getStore().values()] : [];
    measure("subtitleHistory", () => renderSubtitlesTimeline(wall));
    measure("chatOverlay", () => chatOverlay.render(wall, liveMessages, {
      enabled: chatOverlayToggle.checked, translated: el("chatTranslateToggle").checked,
      size: Number(chatOverlaySize.value),
      paused: video.paused, filterPureEmoji: el("hidePureEmojiToggle").checked,
    }));
    if (liveMessagesTimeline) {
      const result = measure("chatHistory", () => liveMessagesTimeline.render(wall, liveMessages, {
        version: liveMessagesClient?.getVersion?.() ?? null,
      }));
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

  /** Keep only a draft that can be placed on the timeline at all. */
  function usableDraft(draft) {
    if (Array.isArray(draft)) return draft.slice(0, 32).map(usableDraft).filter(Boolean);
    if (!draft || typeof draft.text !== "string" || !draft.text.trim()) return null;
    if (!Number.isFinite(draft.tStart) || !Number.isFinite(draft.tEnd)) return null;
    return draft;
  }

  function foldForDraft(text) {
    return String(text || "").normalize("NFKC").replace(/\s+/g, "");
  }

  function draftLine(draft, position, cues) {
    if (Array.isArray(draft)) {
      // A future speaker cannot hide the one currently being heard. Timed
      // previews are preloaded, but never admitted before their onset.
      for (const candidate of [...draft].sort((a, b) => b.tStart - a.tStart)) {
        const line = draftLine(candidate, position, cues);
        if (line) return line;
      }
      return null;
    }
    if (!draft) return null;
    const translated = typeof draft.translation === "string" ? draft.translation.trim() : "";
    // Translation-only mode may draw a draft once the Provider has supplied a
    // trusted translated prefix. Before that, do not substitute source text.
    if (subtitlePrefs.mode === "zh" && !translated) return null;
    if (position < draft.tStart) return null;
    // The backend cut the text at the playhead it was last told about, which is
    // a poll behind this one. The bound absorbs that and hides the line when a
    // seek leaves it behind.
    if (position > draft.tEnd + SUBTITLE_DRAFT_STALE_SECONDS) return null;
    const held = foldForDraft(draft.text);
    // The cue that carries these words replaced the projection on the server.
    for (const cue of cues) {
      if (draft.itemId && cue.itemId && (draft.itemId !== cue.itemId
          || (draft.generation != null && cue.generation != null && draft.generation !== cue.generation))) continue;
      const shown = foldForDraft(cue.src);
      if (shown && (shown.startsWith(held) || held.startsWith(shown))) return null;
    }
    return { source: draft.text, translated };
  }

  function paintDraft(lines) {
    const row = el("subtitleDraft");
    if (!row) return;
    const source = lines?.source || "";
    const translated = lines?.translated || "";
    const zhLine = row.querySelector(".subtitle-zh");
    const srcLine = row.querySelector(".subtitle-src");
    if (zhLine && zhLine.textContent !== translated) zhLine.textContent = translated;
    if (srcLine && srcLine.textContent !== source) srcLine.textContent = source;
    row.hidden = !source && !translated;
    if (zhLine) zhLine.dir = "auto";
    if (srcLine) srcLine.dir = "auto";
  }

  function renderSubtitle(frameMediaTime) {
    const layer = el("subtitleLayer");
    if (!subtitlePrefs.enabled || !subtitleScheduler) { clearSubtitle(); return; }
    const wall = Number.isFinite(frameMediaTime)
      ? mediaClock?.wallTimeForMediaPosition(frameMediaTime)
      : playingWallClock();
    if (!Number.isFinite(wall)) { clearSubtitle(); return; }
    const t = wall + subtitlePrefs.offset + SUBTITLE_RENDER_ADVANCE_SECONDS;
    // Every cue owns its own display window. active() returns all ready cues
    // whose windows contain the playhead, so overlapping speakers/utterances
    // remain visible as independent rows rather than replacing one another.
    const cueValues = [...subtitleCues.values()];
    const cues = typeof subtitleScheduler.active === "function"
      ? subtitleScheduler.active(cueValues, t)
      : [subtitleScheduler.pick(cueValues, t)].filter(Boolean);
    const draft = draftLine(usableDraft(subtitleDraft), t, cues);
    if (!cues.length && !draft) {
      lastSubtitlePaintKey = null;
      clearSubtitle();
      return;
    }
    const targetLanguage = targetSelector?.value || "";
    const paintKey = `${subtitlePrefs.mode}\u001e${targetLanguage}\u001e${cues.map((cue) => [
      cue.id, cue.seq, cue.revision, cue.src, cue.zh,
    ].join("\u001f")).join("\u001e")}\u001e${draft?.translated || ""}\u001f${draft?.source || ""}`;
    // The scheduler still runs every 100ms so admissions follow the playhead,
    // but avoid rebuilding the same DOM rows when the visible cue set did not
    // change. This is the common case between two cue boundaries.
    if (paintKey === lastSubtitlePaintKey) return;
    lastSubtitlePaintKey = paintKey;
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
        row.setAttribute("aria-label", cue.speaker ? updateLabel("timeline.speaker", "说话人 {speaker}", { speaker: cue.speaker }) : updateLabel("timeline.cueLine", "字幕"));
        const zhLine = row.querySelector(".subtitle-zh");
        const srcLine = row.querySelector(".subtitle-src");
        const lines = subtitleLines(cue) || { translated: "", source: "" };
        zhLine.textContent = lines.translated;
        srcLine.textContent = lines.source;
        // Translation-only mode hides the source line. A cue with no translation
        // takes that line over through style.css rather than showing nothing.
        if (lines.translated) delete row.dataset.sourceOnly;
        else row.dataset.sourceOnly = "1";
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
    paintDraft(draft);
  }

  function clearSubtitle() {
    lastSubtitlePaintKey = null;
    const layer = el("subtitleLayer");
    if (!layer.classList.contains("off")) layer.classList.add("off");
    const content = layer.querySelector(".subtitle-content");
    if (content.childElementCount) content.replaceChildren();
    paintDraft(null);
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

  /*
   * A fatal error's `details` alone is often not diagnosable -- the 2026-09-16
   * session left `hls.js fatal: internalException` in the log and nothing else,
   * which names the error without saying anything about it. `internalException`
   * in particular is hls.js's catch-all for an exception thrown inside its own
   * async machinery, so the type, the reason and the underlying error's first
   * stack frame are what make the next occurrence actionable.
   *
   * Only ever called for fatal errors: non-fatal ones fire constantly on a
   * stalling live stream and would drown the ring.
   */
  function describeFatal(data) {
    const parts = [String(data?.details ?? "unknown")];
    parts.push(`type=${data?.type ?? "?"}`);
    if (data?.reason) parts.push(`reason=${data.reason}`);
    const error = data?.error;
    if (error) {
      parts.push(`${error.name || "Error"}: ${error.message || String(error)}`);
      const frame = String(error.stack || "").split("\n").slice(1).find((line) => line.trim());
      if (frame) parts.push(`at ${frame.trim()}`);
    }
    return parts.join(" | ");
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
    el("start").querySelector(".button-label").textContent = starting ? updateLabel("action.starting", "正在启动") : updateLabel("action.startPlayback", "开始播放");
    el("stop").querySelector(".button-label").textContent = stopping ? updateLabel("action.stopping", "正在停止") : updateLabel("action.stop", "停止");
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
    el("setupFeedback").textContent = "点击解析，读取这场直播的清晰度。";
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
      el("message").textContent = `${updateLabel("msg.playStartFailed", "无法开始播放：")}${error.message || error}`;
    }
  });
  // 画面上单击暂停/继续、双击全屏。单击要等一个双击间隔再生效，否则双击会先
  // 把视频暂停再恢复。控制条、字幕窗（可拖动）和开播卡片上的点击不算画面点击；
  // 走按钮自己的 click，按钮 disabled 时也就自然不响应。
  const STAGE_CLICK_DELAY_MS = 250;
  let stageClickTimer = null;
  const isStageSurfaceClick = (event) => event.button === 0
    && !event.target.closest("button, input, select, textarea, a, label, .player-controls, #subtitleLayer, #emptyState");
  stage.addEventListener("click", (event) => {
    if (!isStageSurfaceClick(event)) return;
    clearTimeout(stageClickTimer);
    if (event.detail > 1) return;
    stageClickTimer = setTimeout(() => el("playPause").click(), STAGE_CLICK_DELAY_MS);
  });
  stage.addEventListener("dblclick", (event) => {
    if (!isStageSurfaceClick(event)) return;
    clearTimeout(stageClickTimer);
    event.preventDefault();
    el("toggleFullscreen").click();
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
  el("cookiePayload").addEventListener("input", detectCookieFormat);
  el("proxyMode")?.addEventListener("change", updateProxyModeUi);
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
  // A play the viewer started stands the automatic stall pause down. Without
  // this the 1s status poll re-paused the element, so during a stall that never
  // cleared the play button did nothing at all.
  video.addEventListener("play", () => {
    if (autoResumeInFlight) {
      autoResumeInFlight = false;
      return;
    }
    stallAutoPauseSuppressed = true;
  });
  video.addEventListener("timeupdate", notePlaybackProgress);
  video.addEventListener("pause", () => { resetSubtitleBudget(); updatePlayerControls(); revealPlayerControls(); });
  video.addEventListener("seeking", resetSubtitleBudget);
  video.addEventListener("ratechange", resetSubtitleBudget);
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
    subtitleCueOrderDirty = true;
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
  window.__lingerlensSubtitleReadiness = () => [...subtitleReadiness.values()].map((sample) => ({
    ...sample, source: sample.source && { ...sample.source }, translation: sample.translation && { ...sample.translation },
  }));
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
    // A cue that arrives just after a poll used to wait up to 500ms before the
    // renderer could even consider it. The local endpoint is cheap and the
    // poller is serial, so 250ms keeps onset latency below one render beat
    // without creating overlapping requests.
    intervalMs: 250,
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
  subtitleRenderTimer = window.createSubtitleRenderLoop({
    video,
    render: (position) => {
      if (playbackProbe) playbackProbe.measure("subtitle", () => renderSubtitle(position));
      else renderSubtitle(position);
    },
    tick: () => playbackProbe?.tick(),
    isHidden: () => document.hidden,
  });
  // Keep the 100ms subtitle layer responsive, but move the heavier history and
  // chat DOM pass off that cadence. Starting it half a beat later prevents the
  // two timers from repeatedly landing on the same event-loop turn.
  window.setTimeout(() => {
    timelineRenderTimer = setInterval(() => { if (!document.hidden) {
      renderTimelines();
      if (playbackProbe) playbackProbe.measure("controls", updatePlayerControls);
      else updatePlayerControls();
    } }, 333);
  }, 125);
  if (liveMessagesClient) liveMessagesClient.startPolling(500);
  document.addEventListener("visibilitychange", () => {
    resetSubtitleBudget();
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
    subtitleRenderTimer?.stop();
    clearInterval(timelineRenderTimer);
    playbackProbe?.dispose();
    netProbe?.restore();
    if (liveMessagesClient) liveMessagesClient.stopPolling();
  });
})();
