(function (global) {
  "use strict";

  /*
   * 运行诊断：一条给用户看的记录流。
   *
   * 三条来源合成同一条时间线：
   *   1. 渲染进程自己的事件（player.js 的报错、媒体错误、恢复动作、轮询失败）；
   *   2. 后端 /api/logs 的环形缓冲（FFmpeg、媒体会话生命周期、400/500 请求失败）；
   *   3. 上面两者都不覆盖的 ffmpegLogTail —— 后端在会话出错时把它作为 source
   *      "media" 的记录发出来，所以这里不需要单独处理。
   *
   * 后端记录带 seq，用它去重；渲染进程记录没有 seq，用本地自增 id。同一条记录
   * 可能被两边各写一次（例如 showError 和后端的 400 日志），按 level+source+
   * message 的短期指纹去重。
   */

  const DEFAULT_CAPACITY = 400;
  const LEVEL_RANK = { info: 0, warn: 1, error: 2 };
  const LEVELS = Object.keys(LEVEL_RANK);

  function levelRank(level) {
    return LEVEL_RANK[level] ?? 0;
  }
  /* 同一条消息在这个窗口内重复出现只保留一次：轮询失败会以 500ms 的频率刷屏。 */
  const DEDUPE_WINDOW_SECONDS = 5;

  const LEVEL_KEYS = { info: "diag.level.info", warn: "diag.level.warn", error: "diag.level.error" };
  const LEVEL_FALLBACK = { info: "信息", warn: "警告", error: "错误" };

  /*
   * 已知故障特征 → 一句人话交代。顺序敏感，先匹配到的赢。
   * 这里刻意只给「看懂了之后能做什么」，不给复述错误本身。
   * fallback 同时是 I18N 缺席时的文案，所以模块可以脱离页面单测。
   */
  const HINTS = [
    { test: /FFmpeg exited with code|FFmpeg stopped unexpectedly|ffmpeg exited/i, key: "diag.hint.media",
      fallback: "直播源下载中断。多数是直播已结束，或平台限制了这次拉流——停止后重新启动会话即可。" },
    { test: /Failed to fetch|NetworkError|ERR_NETWORK|Backend unavailable|HTTP 5\d\d/, key: "diag.hint.network",
      fallback: "本地后台没有响应。确认 LingerLens 仍在运行，然后重试。" },
    /* 必须排在 network 之后：`Backend unavailable` 会被上面那条接住，而它的
       处置办法（重试）和这条（换链接）完全不同。 */
    { test: /probe failed|no video formats|video is unavailable|live event has ended|unable to extract|not available in your country|requested format is not available|private video/i, key: "diag.hint.source",
      fallback: "这个链接拉不到直播。多半是链接已失效、直播已经结束，或者平台要求登录、限制了访问——换一个正在直播的链接再试。" },
    { test: /hls\.js fatal|MEDIA_ERROR|无法恢复|不支持 MSE/i, key: "diag.hint.decode",
      fallback: "播放器无法解码这路直播。试一下更低的清晰度。" },
    { test: /翻译失败|translation.*fail|translationLastFailure/i, key: "diag.hint.translation",
      fallback: "翻译正在失败。到「连接与密钥」检查翻译厂商的密钥和额度。" },
    { test: /cookie|Cookie|登录|unauthorized|401|403/i, key: "diag.hint.auth",
      fallback: "这路直播需要登录。导入对应平台的 Cookie 后重试。" },
  ];

  function translate(key, fallback, vars) {
    const api = global.I18N;
    if (api && typeof api.t === "function") {
      const value = api.t(key, vars, fallback);
      if (typeof value === "string" && value) return value;
    }
    if (!vars) return fallback;
    return Object.entries(vars).reduce((text, [name, value]) => text.replace(`{${name}}`, value), fallback);
  }

  /* 单行、限长。省略号算在 400 以内，这样截断后的长度仍然可预测。 */
  const MAX_MESSAGE = 400;

  function normalizeMessage(value) {
    const text = String(value ?? "").replace(/[\r\n]+/g, " ").trim();
    if (text.length <= MAX_MESSAGE) return text;
    return `${text.slice(0, MAX_MESSAGE - 1)}…`;
  }

  function hintFor(message) {
    const rule = HINTS.find((candidate) => candidate.test.test(String(message)));
    if (!rule) return null;
    return translate(rule.key, rule.fallback) || null;
  }

  function createDiagnosticsLog(options = {}) {
    const capacity = Math.max(1, Number(options.capacity ?? DEFAULT_CAPACITY));
    const now = options.now || (() => Date.now() / 1000);
    let records = [];
    let clientSeq = 0;
    /* 插入序，只用来在时间戳相同时稳定排序。不能用 id 排序：'c10' < 'c2'。 */
    let insertion = 0;
    let sessionId = null;
    const seenBackend = new Set();

    function trim() {
      if (records.length <= capacity) return;
      records = records.sort(order).slice(-capacity);
    }

    function order(a, b) {
      return a.t - b.t || a.order - b.order;
    }

    function fingerprint(record) {
      return `${record.level}\u0000${record.source}\u0000${record.message}`;
    }

    function isDuplicate(record) {
      return records.some((existing) =>
        existing.t <= record.t + DEDUPE_WINDOW_SECONDS
        && existing.t >= record.t - DEDUPE_WINDOW_SECONDS
        && fingerprint(existing) === fingerprint(record));
    }

    /* 客户端记录：返回写入的那条，调用方可以据此决定要不要展开诊断栏。 */
    function record(level, source, message) {
      const text = normalizeMessage(message);
      if (!text) return null;
      const entry = {
        id: `c${++clientSeq}`,
        t: now(),
        level: LEVELS.includes(level) ? level : "info",
        source: String(source || "app"),
        message: text,
      };
      entry.rank = levelRank(entry.level);
      entry.order = ++insertion;
      if (isDuplicate(entry)) return null;
      records.push(entry);
      trim();
      return entry;
    }

    /* 后端记录：seq 是权威去重键，跨轮询不会重复计入。 */
    function merge(incoming) {
      const added = [];
      for (const raw of Array.isArray(incoming) ? incoming : []) {
        const seq = Number(raw?.seq);
        if (!Number.isFinite(seq) || seenBackend.has(seq)) continue;
        const message = normalizeMessage(raw?.message);
        if (!message) continue;
        seenBackend.add(seq);
        const entry = {
          id: `b${seq}`,
          seq,
          t: Number.isFinite(Number(raw?.t)) ? Number(raw.t) : now(),
          level: LEVELS.includes(raw?.level) ? raw.level : "info",
          source: String(raw?.source || "backend"),
          message,
        };
        entry.rank = levelRank(entry.level);
        entry.order = ++insertion;
        records.push(entry);
        added.push(entry);
      }
      trim();
      return added;
    }

    function list() {
      return records.slice().sort(order);
    }

    function counts() {
      const result = { info: 0, warn: 0, error: 0, total: records.length };
      for (const entry of records) result[entry.level] += 1;
      return result;
    }

    /* 诊断栏的色调只看最高级别：一旦出过错，就需要用户「清空」来确认。 */
    function worstLevel() {
      return records.reduce((worst, entry) => (entry.rank > levelRank(worst) ? entry.level : worst), "info");
    }

    function newest(level) {
      const pool = level ? records.filter((entry) => entry.level === level) : records;
      return pool.length ? pool.slice().sort(order)[pool.length - 1] : null;
    }

    /*
     * 摘要该显示哪一条。优先挑「能给出人话交代」的那条：后端会把 FFmpeg 的
     * 尾部输出也标成 error，而 `frame= 12 fps=0 …` 远不如
     * `FFmpeg exited with code 1` 有用，按时间取最新会把真正的原因顶掉。
     */
    function lead() {
      const rows = list();
      const problems = rows.filter((entry) => entry.level === "error");
      const pool = problems.length ? problems : rows.filter((entry) => entry.level === "warn");
      if (!pool.length) return null;
      for (let index = pool.length - 1; index >= 0; index -= 1) {
        if (hintFor(pool[index].message)) return pool[index];
      }
      return pool[pool.length - 1];
    }

    return {
      record,
      merge,
      list,
      counts,
      worstLevel,
      newest,
      lead,
      clear() {
        records = [];
        clientSeq = 0;
        insertion = 0;
        seenBackend.clear();
      },
      setSessionId(value) { sessionId = value ?? null; },
      /* 后端重启会让 seq 归零，此时必须清空去重表，否则新记录会被当成旧的丢掉。 */
      getSessionId() { return sessionId; },
      get capacity() { return capacity; },
      get size() { return records.length; },
    };
  }

  /* 后端 /api/logs 的轮询客户端。seq 归零 = 后端换了进程，交回调用方重置。 */
  function createDiagnosticsClient(options = {}) {
    const fetchFn = options.fetch || (typeof global.fetch === "function" ? global.fetch.bind(global) : null);
    const onUpdate = options.onUpdate || (() => {});
    let afterSeq = 0;
    let restartCount = 0;

    return {
      async poll() {
        try {
          const response = await fetchFn(`/api/logs?afterSeq=${afterSeq}`, { cache: "no-store" });
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const data = await response.json();
          /*
           * 环形缓冲的 seq 是连续的，缺口就是被淘汰掉的行。首轮不报：那只是
           * 「页面比后端晚开」，不是这次运行丢了东西。会话中途出现缺口才是
           * 真丢了：标签页被挂起时轮询降频，500 条很容易被写满。
           */
          const previous = afterSeq;
          const first = Array.isArray(data.records) && data.records.length ? Number(data.records[0]?.seq) : NaN;
          const missed = previous > 0 && Number.isFinite(first) && first > previous + 1 ? first - previous - 1 : 0;
          if (Number.isFinite(Number(data.maxSeq))) afterSeq = Math.max(afterSeq, Number(data.maxSeq));
          onUpdate({ ...data, missed, ok: true });
          return { ...data, missed, ok: true };
        } catch (error) {
          const result = { records: [], maxSeq: afterSeq, dropped: 0, missed: 0, ok: false, error: error.message || String(error) };
          onUpdate(result);
          return result;
        }
      },
      /* 后端换了进程时调用：seq 空间从头开始，必须跟着归零。 */
      resetForRestart() {
        afterSeq = 0;
        restartCount += 1;
      },
      getAfterSeq: () => afterSeq,
      getRestartCount: () => restartCount,
    };
  }

  /*
   * 更新接口。它由主进程提供，不经过后端：只有主进程能替换正在运行的可执行
   * 文件，所以这些路由在 protocol.handle 里就被截下了。
   *
   * `status` 同时带回这份构建的身份（版本、是否打包、构建时间）—— 这正是
   * 「我手上这份是不是最新」唯一能一眼回答的地方。
   */
  function createUpdateClient(options = {}) {
    const fetchFn = options.fetch || (typeof global.fetch === "function" ? global.fetch.bind(global) : null);
    const onUpdate = options.onUpdate || (() => {});
    let latest = null;

    async function call(path, method) {
      const response = await fetchFn(path, { method, cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      // 502 是「下载失败」，它带着可读的原因，不是传输层错误。
      if (!response.ok && response.status !== 502) throw new Error(`HTTP ${response.status}`);
      return data;
    }

    function publish(data) {
      latest = data;
      onUpdate(data);
      return data;
    }

    return {
      async status() {
        try {
          return publish(await call("/api/app-update", "GET"));
        } catch (error) {
          return publish({ error: error.message || String(error), update: { status: "failed" } });
        }
      },
      async check() {
        try {
          return publish({ ...(latest || {}), update: await call("/api/app-update/check", "POST") });
        } catch (error) {
          return publish({ ...(latest || {}), update: { status: "failed", error: error.message || String(error) } });
        }
      },
      async install() {
        try {
          return publish({ ...(latest || {}), update: await call("/api/app-update/install", "POST") });
        } catch (error) {
          return publish({ ...(latest || {}), update: { status: "failed", error: error.message || String(error) } });
        }
      },
      get: () => latest,
    };
  }

  function createDiagnosticsBar(options = {}) {
    const doc = options.document || global.document;
    const log = options.log;
    const root = options.root || doc?.getElementById("diagnosticsBar");
    if (!root || !log) return null;
    const toggle = doc.getElementById("diagToggle");
    const body = doc.getElementById("diagBody");
    const listNode = doc.getElementById("diagList");
    const summaryNode = doc.getElementById("diagSummary");
    const countNode = doc.getElementById("diagCount");
    const hintNode = doc.getElementById("diagHint");
    const emptyNode = doc.getElementById("diagEmpty");
    const onCopy = options.onCopy || (() => {});
    let expanded = false;
    let renderedSignature = null;

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (character) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
    }

    function formatClock(epochSeconds) {
      const date = new Date(epochSeconds * 1000);
      const pad = (value) => String(value).padStart(2, "0");
      return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
    }

    function levelLabel(level) {
      return translate(LEVEL_KEYS[level], LEVEL_FALLBACK[level] || level);
    }

    function setExpanded(next) {
      expanded = Boolean(next);
      root.dataset.expanded = expanded ? "true" : "false";
      toggle?.setAttribute("aria-expanded", expanded ? "true" : "false");
      if (body) body.hidden = !expanded;
      if (expanded) render(true);
    }

    function render(force) {
      const rows = log.list();
      const counts = log.counts();
      root.dataset.tone = counts.total === 0 ? "idle" : log.worstLevel();
      const lead = typeof log.lead === "function" ? log.lead() : (log.newest("error") || log.newest("warn"));
      const hint = lead ? hintFor(lead.message) : null;

      /* 摘要与计数每次都重算：它们便宜，而且丢一次更新比多刷一次 DOM 更糟。 */
      if (summaryNode) {
        summaryNode.textContent = lead ? lead.message : translate("diag.ok", "一切正常");
        summaryNode.title = lead ? lead.message : "";
      }
      if (countNode) {
        countNode.textContent = String(counts.total);
        countNode.hidden = counts.total === 0;
      }
      if (hintNode) {
        hintNode.textContent = hint || "";
        hintNode.hidden = !hint;
      }
      if (emptyNode) emptyNode.hidden = rows.length > 0;

      if (!listNode || !expanded) return;
      /* 只有列表体做签名短路。首尾 id 一起看：环形缓冲满时新增一条会同时淘汰
         一条，长度和最后一条都不变，只看这两样会漏掉更新。 */
      const signature = `${rows.length}|${rows.length ? rows[0].id : ""}|${rows.length ? rows[rows.length - 1].id : ""}`;
      if (!force && signature === renderedSignature) return;
      renderedSignature = signature;
      listNode.innerHTML = rows.map((entry) =>
        `<li class="diag-row" data-level="${escapeHtml(entry.level)}">`
        + `<span class="diag-time mono">${formatClock(entry.t)}</span>`
        + `<span class="diag-level mono">${escapeHtml(levelLabel(entry.level))}</span>`
        + `<span class="diag-source mono">${escapeHtml(entry.source)}</span>`
        + `<span class="diag-text" dir="auto">${escapeHtml(entry.message)}</span>`
        + "</li>").join("");
      listNode.scrollTop = listNode.scrollHeight;
    }

    /* 只有 error 会自动展开：警告刷屏时把面板顶开会比问题本身更烦人。 */
    function push(level, source, message) {
      const entry = log.record(level, source, message);
      if (!entry) return null;
      if (entry.level === "error" && !expanded) setExpanded(true);
      else render();
      return entry;
    }

    toggle?.addEventListener("click", () => setExpanded(!expanded));
    doc.getElementById("diagCopy")?.addEventListener("click", () => onCopy());
    doc.getElementById("diagClear")?.addEventListener("click", () => {
      log.clear();
      render(true);
    });

    render(true);
    /* 收起状态由模块自己落实，不依赖 HTML 上的 hidden 属性：否则任何一处
       标记遗漏都会让面板一开页就摊开。 */
    setExpanded(false);
    return {
      render,
      push,
      expand: () => setExpanded(true),
      collapse: () => setExpanded(false),
      isExpanded: () => expanded,
      refreshLocale: () => render(true),
    };
  }

  /* 本地时间的秒级时间戳。复制报告和落盘日志共用同一个格式。 */
  function stampSeconds(epochSeconds) {
    const date = new Date(epochSeconds * 1000);
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
      + `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }

  /*
   * 落盘用的行格式。故意不做本地化：这份文件是拿来 grep 和转交的，所以级别
   * 保持 `error` 这样的稳定 token，哪怕界面是中文。给人读的那份是
   * formatDiagnostics —— 那个要贴进 issue，所以它才需要翻译。
   */
  function formatRecordLines(rows) {
    return (Array.isArray(rows) ? rows : []).map((entry) =>
      `${stampSeconds(entry.t)}  [${entry.level}] ${entry.source}  ${entry.message}`);
  }

  /*
   * 复制到剪贴板的诊断文本。故意用纯文本而不是 JSON：用户会直接贴进 issue，
   * 而 issue 里需要的是能读的上下文，不是让人再去解析一遍的结构。
   */
  function formatDiagnostics(options = {}) {
    const log = options.log;
    const context = options.context || {};
    const title = options.title || "LingerLens 诊断信息";
    const timeLabel = options.timeLabel || "生成时间";
    const countLabel = options.countLabel || "记录";
    const levelLabels = options.levelLabels || LEVEL_FALLBACK;
    const rows = log ? log.list() : [];
    const counts = log ? log.counts() : { info: 0, warn: 0, error: 0, total: 0 };
    const header = [title, `${timeLabel}: ${stampSeconds(Date.now() / 1000)}`];
    for (const [label, value] of Object.entries(context)) {
      if (value === null || value === undefined || value === "") continue;
      header.push(`${label}: ${value}`);
    }
    const summary = `${countLabel}: ${counts.total}`
      + ` (${levelLabels.error} ${counts.error} / ${levelLabels.warn} ${counts.warn} / ${levelLabels.info} ${counts.info})`;
    header.push(summary, "");
    const body = rows.map((entry) =>
      `${stampSeconds(entry.t)}  [${levelLabels[entry.level] || entry.level}] ${entry.source}  ${entry.message}`);
    return header.concat(body).join("\n");
  }

  /*
   * 开发日志落盘。文件归主进程所有，渲染进程在沙箱里既没有文件系统也没有
   * preload/IPC，所以记录只能走 lingerlens:// 这条早就存在的通道交过去。
   *
   * status() 顺带当特性开关用：没配日志文件时 enabled 为 false，之后一条
   * 都不会发。send() 返回真正落盘的行数——调用方只有在它等于待发条数时才能
   * 推进游标，否则记录就永久丢了。
   */
  function createDevLogClient(options = {}) {
    const fetchFn = options.fetch || (typeof global.fetch === "function" ? global.fetch.bind(global) : null);
    const off = { enabled: false, file: null, error: null, lines: 0, terminalOnly: false, active: false };
    let state = null;

    async function status() {
      try {
        const response = await fetchFn("/api/diagnostics", { cache: "no-store" });
        const data = await response.json().catch(() => ({}));
        state = { ...off, ...data };
      } catch {
        state = { ...off };
      }
      return state;
    }

    /* 三个控制动作都返回新的完整状态，调用方直接拿它渲染，不用再问一次。 */
    async function control(action) {
      try {
        const response = await fetchFn(`/api/diagnostics/${action}`, { method: "POST", cache: "no-store" });
        const data = await response.json().catch(() => ({}));
        state = { ...off, ...state, ...data };
      } catch (error) {
        state = { ...off, ...state, error: error.message || String(error) };
      }
      return state;
    }

    return {
      status,
      get: () => state,
      start: () => control("start"),
      stop: () => control("stop"),
      reveal: () => control("reveal"),
      async send(lines) {
        const payload = Array.isArray(lines) ? lines.filter(Boolean) : [];
        if (!state?.enabled || !payload.length) return 0;
        try {
          const response = await fetchFn("/api/diagnostics", {
            method: "POST", cache: "no-store",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lines: payload }),
          });
          const data = await response.json().catch(() => ({}));
          state = { ...state, ...data };
          return Number(data.written) || 0;
        } catch {
          return 0;
        }
      },
    };
  }

  const exported = {
    createDiagnosticsLog,
    createDiagnosticsClient,
    createDiagnosticsBar,
    createUpdateClient,
    createDevLogClient,
    formatDiagnostics,
    formatRecordLines,
    hintFor,
  };
  global.LingerLensDiagnostics = exported;
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
})(typeof window !== "undefined" ? window : globalThis);
