(function (global) {
  "use strict";
  // i18n.js publishes itself on window before this runs; the Chinese stays in the
  // call so a missing key degrades to the text this file was written with.
  const t = (key, text) => (window.I18N ? window.I18N.t(key, null, text) : text);

  function createLiveMessagesClient(options = {}) {
    const fetchFn = options.fetch || (typeof window !== "undefined" ? window.fetch.bind(window) : null);
    const request = options.request;
    const onUpdate = options.onUpdate || (() => {});
    const baseUrl = options.baseUrl || "";
    const retentionSeconds = Math.max(0, Number(options.retentionSeconds ?? 120));
    const maxMessages = Math.max(1, Number(options.maxMessages ?? 500));
    const store = new Map();
    let afterSeq = 0;
    let maxSeq = 0;
    let stats = null;
    let storeVersion = 0;
    let timer = null;
    let polling = false;
    let pollingGeneration = 0;
    // Session counters, never reset: the probe diffs them per window, and a
    // counter that resets mid-session would read as a negative delta.
    let polls = 0;
    let received = 0;
    let added = 0;

    async function get(path) {
      if (request) return request(path);
      const response = await fetchFn(`${baseUrl}${path}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    }

    async function post(path, body) {
      if (request) return request(path, body);
      const response = await fetchFn(`${baseUrl}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    }

    function pruneStore() {
      let newestMediaTime = -Infinity;
      for (const message of store.values()) {
        const mediaTime = Number(message?.mediaTime);
        if (Number.isFinite(mediaTime)) newestMediaTime = Math.max(newestMediaTime, mediaTime);
      }
      const cutoff = newestMediaTime - retentionSeconds;
      for (const [id, message] of store) {
        const mediaTime = Number(message?.mediaTime);
        if ((Number.isFinite(cutoff) && Number.isFinite(mediaTime) && mediaTime < cutoff) || store.size > maxMessages) {
          store.delete(id);
          storeVersion += 1;
        }
      }
    }

    const api = {
      async poll() {
        polls += 1;
        try {
          const data = await get(`/api/live-messages?afterSeq=${afterSeq}`);
          const batch = data.messages || [];
          // `got` is what the backend sent, `added` is what the store actually
          // took. A wide gap between them means we are being handed the same
          // messages over and over, which is its own kind of danmaku flood.
          received += batch.length;
          for (const incoming of batch) {
            const current = store.get(incoming.id);
            if (!current || Number(incoming.seq) > Number(current.seq) || Number(incoming.revision) >= Number(current.revision)) {
              store.set(incoming.id, current ? { ...current, ...incoming } : incoming);
              storeVersion += 1;
              added += 1;
            }
          }
          if (Number.isFinite(Number(data.maxSeq))) {
            maxSeq = Math.max(maxSeq, Number(data.maxSeq));
            afterSeq = maxSeq;
          }
          pruneStore();
          stats = data.stats || stats;
          onUpdate({ ...data, stats });
          return data;
        } catch (error) {
          const result = { error: error.message || String(error), messages: [], maxSeq, stats };
          onUpdate(result);
          return result;
        }
      },
      startPolling(intervalMs = 500) {
        if (polling) return;
        polling = true;
        const generation = ++pollingGeneration;
        const tick = async () => {
          if (!polling || generation !== pollingGeneration) return;
          await api.poll();
          if (polling && generation === pollingGeneration) timer = setTimeout(tick, Math.max(100, Number(intervalMs) || 500));
        };
        tick();
      },
      stopPolling() {
        polling = false;
        pollingGeneration += 1;
        if (timer) clearTimeout(timer);
        timer = null;
      },
      async setTranslate(enabled) {
        const data = await post("/api/live-messages/settings", { translate: Boolean(enabled) });
        stats = data.stats || stats;
        onUpdate({ ...data, stats });
        return data;
      },
      getStore: () => store,
      getVersion: () => storeVersion,
      getStats: () => stats,
      getCounters: () => ({ poll: polls, got: received, added }),
      getMaxSeq: () => maxSeq,
      reset() {
        afterSeq = 0;
        maxSeq = 0;
        stats = null;
        store.clear();
        storeVersion += 1;
      },
    };
    return api;
  }

  /* 稳定的字符串散列：只用来给弹幕挑一个固定色调，不做任何安全用途。 */
  function hashText(value) {    let hash = 0;
    for (let index = 0; index < value.length; index += 1) {
      hash = (hash * 31 + value.charCodeAt(index)) | 0;
    }
    return Math.abs(hash);
  }

  function isPureEmojiText(text) {
    if (!text) return false;
    const trimmed = String(text).trim();
    if (!trimmed) return false;
    const stripped = trimmed
      .replace(/:[a-zA-Z0-9_+-]+:/g, "")
      .replace(/\p{Extended_Pictographic}/gu, "")
      .replace(/[‍︎️\s]/g, "");
    return stripped.length === 0;
  }

  function createLiveMessagesTimeline(options = {}) {
    const maxDomItems = options.maxDomItems || 500;
    const container = options.container || null;
    let source = options.source || (() => []);
    let filterPureEmoji = Boolean(options.filterPureEmoji);
    let lastSignature = null;
    // Cumulative: one `innerHTML` write is one node subtree thrown away and
    // rebuilt, and that is the timeline's real cost -- not the JS that decides
    // to do it. Not reset by clear(), for the same delta reason as the overlay.
    let rewrites = 0;
    // The store is polled much more often than its ordering changes. Keep a
    // sorted snapshot so moving the playback clock does not sort hundreds of
    // messages again on every UI tick.
    let sortedSource = [];
    let sourceSignature = null;
    let sourceVersion = null;

    function prepare(messages, version = null) {
      if (!Array.isArray(messages)) return [];
      if (version !== null && version === sourceVersion) return sortedSource;
      const signature = messages.map((message) => `${message?.id}:${message?.seq}:${message?.revision}:${message?.mediaTime}:${message?.kind || ""}`).join("|");
      if (signature !== sourceSignature || (version !== null && version !== sourceVersion)) {
        sortedSource = messages.slice().sort((a, b) => Number(a.mediaTime) - Number(b.mediaTime) || Number(a.seq) - Number(b.seq));
        sourceSignature = signature;
      }
      sourceVersion = version;
      return sortedSource;
    }

    function visible(messages, playbackWallTime, version = null) {
      if (!Array.isArray(messages) || !Number.isFinite(playbackWallTime)) return [];
      const sorted = prepare(messages, version);
      const cutoff = playbackWallTime + 0.25;
      // Find the first future message without scanning and sorting the tail.
      let low = 0;
      let high = sorted.length;
      while (low < high) {
        const middle = (low + high) >> 1;
        if (Number(sorted[middle].mediaTime) <= cutoff) low = middle + 1;
        else high = middle;
      }
      return sorted.slice(0, low)
        .filter((message) => (!message?.kind || message.kind === "text")
          && (!filterPureEmoji || !isPureEmojiText(message.text)))
        .slice(-maxDomItems);
    }

    return {
      setSource(nextSource) { source = nextSource; },
      setFilterPureEmoji(enabled) { filterPureEmoji = Boolean(enabled); },
      getVisibleMessages: visible,
      getStats: () => ({ rewrites }),
      render(playbackWallTime, messages = source(), options = {}) {
        const rows = visible(messages, playbackWallTime, options.version ?? null);
        const signature = rows.map((message) => `${message.id}:${message.seq}:${message.revision}:${message.translationState || ""}`).join("|");
        const changed = signature !== lastSignature;
        lastSignature = signature;
        if (!container || !changed) return { rows, changed };
        const existing = new Map([...container.children].filter((node) => node.dataset?.messageId).map((node) => [node.dataset.messageId, node]));
        const keep = new Set(rows.map((message) => message.id));
        for (const [id, node] of existing) if (!keep.has(id)) node.remove();
        if (!rows.length) {
          if (!container.querySelector(".timeline-empty")) container.innerHTML = `<div class="timeline-empty">${t("empty.chat.none", "当前画面尚无直播消息")}</div>`;
          return { rows, changed };
        }
        container.querySelector(".timeline-empty")?.remove();
        for (const message of rows) {
          let row = existing.get(message.id);
          if (!row) {
            row = document.createElement("article");
            row.className = "timeline-row live-message-row";
            row.dataset.messageId = message.id;
            container.append(row);
          }
          const rowRevision = `${message.seq}:${message.revision}:${message.translationState || ""}`;
          if (row.dataset.renderRevision === rowRevision) continue;
          const author = message.author && typeof message.author === "object"
            ? message.author
            : { name: message.author || "Anonymous", badges: [] };
          const badges = (author.badges || []).map((badge) => `<small>${escapeHtml(badge)}</small>`).join("");
          row.innerHTML = `<div class="timeline-time">${formatTime(message.mediaTime)}</div><div class="timeline-body"><div class="timeline-author">${escapeHtml(author.name || "Anonymous")}${badges}</div><div class="timeline-text-source" dir="auto">${escapeHtml(message.text)}</div>${message.translationState === "done" && message.translation ? `<div class="timeline-text-translated" dir="auto">${escapeHtml(message.translation)}</div>` : ""}</div>`;
          row.dataset.renderRevision = rowRevision;
          rewrites += 1;
        }
        return { rows, changed };
      },
      clear() {
        lastSignature = null;
        if (container) container.innerHTML = `<div class="timeline-empty">${t("empty.chat.waiting", "等待直播消息…")}</div>`;
      },
    };
  }

  function createChatOverlay({ container, maxRows = 6 } = {}) {
    const seen = new Set();
    let lanes = [], previousWall = null;
    // Cumulative session counters, deliberately NOT reset by clear(): the probe
    // reads them as deltas, and a counter that drops to zero mid-stream would
    // be reported as a negative window. They also read better in the indicator,
    // where "画面省略: 312" should mean the whole session, not since the last seek.
    let dropped = 0, launched = 0, reflows = 0, reflowMs = 0;
    function remember(id) { seen.add(id); if (seen.size > 1000) seen.delete(seen.values().next().value); }
    function clear() {
      container.replaceChildren();
      lanes = [];
      seen.clear();
      previousWall = null;
    }
    return {
      clear,
      getStats: () => ({ dropped, launched, reflows, reflowMs: Math.round(reflowMs * 10) / 10 }),
      render(wall, messages, { enabled = true, translated = false, paused = false, filterPureEmoji = false, size = 1 } = {}) {
        const scale = Math.max(0.75, Math.min(2, Number(size) || 1));
        if (!enabled || !Number.isFinite(wall)) { if (previousWall !== null) clear(); return; }
        if (previousWall !== null && (wall < previousWall - 0.5 || wall > previousWall + 5)) clear();
        previousWall = wall;
        container.classList.toggle("paused", paused);
        if (paused) return;
        const width = container.clientWidth;
        // Fixed maximum-size lanes let existing messages finish during slider input.
        const rowHeight = 64;
        const rowCount = Math.min(maxRows, Math.floor(container.clientHeight / rowHeight));
        if (!width || !rowCount) return;
        const speed = Math.max(90, width / 7);
        const reducedMotion = global.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
        let launched = false;
        // Sort only candidates due now, not the full retained history each tick.
        const ordered = messages.filter(message => {
          if (seen.has(message.id) || message.mediaTime == null || message.mediaTime > wall) return false;
          if (wall - message.mediaTime > 0.75) { remember(message.id); dropped += 1; return false; }
          return true;
        })
          .sort((a, b) => Number(a.mediaTime) - Number(b.mediaTime) || Number(a.seq || 0) - Number(b.seq || 0));
        for (const message of ordered) {
          if (seen.has(message.id) || message.mediaTime == null || message.mediaTime > wall) continue;
          if (wall - message.mediaTime > 0.75) { remember(message.id); dropped += 1; continue; }
          if (filterPureEmoji && isPureEmojiText(message.text)) continue;
          const text = translated ? (message.translationState === "done" ? message.translation : null) : message.text;
          if (!text || message.kind && message.kind !== "text") continue;
          const lane = Array.from({ length: rowCount }, (_, i) => i).find(i => (lanes[i] || 0) <= wall);
          // Bound DOM even with rapid batches; the full history remains in the timeline.
          if (lane === undefined || container.children.length >= 40 || launched) break;
          const node = document.createElement("span");
          // 原型中约 1/3 的弹幕带色；用消息 id 的散列而不是随机数，同一条
          // 消息在重放时保持同一个颜色，不会跳色。只写 className，不用
          // classList：弹幕节点需要的最小 DOM 表面越小越好。
          const tint = hashText(String(message.id)) % 100;
          node.className = `chat-bullet${tint >= 80 ? " dm-lime" : tint >= 65 ? " dm-yellow" : ""}`;
          node.textContent = text;
          node.style.top = `${lane * rowHeight}px`;
          node.style.setProperty("--chat-size", String(scale));
          node.style.maxWidth = `${width}px`;
          node.style.animationName = "none";
          container.append(node);
          // 这一读是强制同步布局：append 刚让样式和布局失效，浏览器必须在这里
          // 把账一次结清。它是整个弹幕层里唯一比周围 JS 更贵的地方，所以计时。
          const reflowStart = nowMs();
          const textWidth = node.getBoundingClientRect().width;
          reflowMs += nowMs() - reflowStart;
          reflows += 1;
          node.style.setProperty("--chat-distance", `${-(width + textWidth)}px`);
          node.style.animationDuration = `${(width + textWidth) / speed}s`;
          node.style.animationName = "";
          node.addEventListener("animationend", () => node.remove(), { once: true });
          lanes[lane] = wall + (reducedMotion ? width + textWidth : textWidth + 40) / speed;
          launched += 1;
          remember(message.id);
          // At most one launch per existing render tick; never catch up by
          // moving an immutable media timestamp seconds into the future.
          break;
        }
      },
    };
  }

  /* 强制重排的耗时必须真的计时，所以这里要一个时钟。拿不到 performance 时
     退化成 0：宁可在日志里显示"没测到"，也不要报一个看起来像真的假数字。 */
  const nowMs = () => (typeof performance !== "undefined" && performance.now ? performance.now() : 0);
  const formatTime = (seconds) => global.LingerLensMediaClock?.formatWallClockTime(seconds) || "--:--:--";
  const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
  const exported = { createLiveMessagesClient, createLiveMessagesTimeline, createChatOverlay };
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else {
    global.LingerLensLiveMessages = exported;
    global.createLiveMessagesClient = createLiveMessagesClient;
    global.createLiveMessagesTimeline = createLiveMessagesTimeline;
    global.createChatOverlay = createChatOverlay;
  }
})(typeof window !== "undefined" ? window : globalThis);


