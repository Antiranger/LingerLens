(function (global) {
  "use strict";

  // 侧栏宽度拖拽。原型里侧栏是死宽 320px，这是原型没有的交互，所以硬约束是：
  // 没拖过的时候必须与原型逐像素一致 —— 手柄透明、不占位，宽度只在用户真的
  // 拖过之后才写进 --pane-left-w / --pane-right-w。
  const STORAGE_KEY = "laglingo.paneWidths";
  // 左栏手柄挂在它右缘，右栏手柄挂在它左缘；拖左栏右移变宽，右栏相反。
  const EDGES = {
    subtitles: { side: "left", variable: "--pane-left-w", pane: "paneSubtitles", other: "chat" },
    chat: { side: "right", variable: "--pane-right-w", pane: "paneChat", other: "subtitles" },
  };
  const LIMITS = { min: 240, max: 560, minStage: 320, step: 16, coarseStep: 64 };

  function clampPaneWidth(value, bounds) {
    const min = bounds.min;
    const max = bounds.max < min ? min : bounds.max;
    if (!Number.isFinite(value)) return min;
    return Math.min(Math.max(Math.round(value), min), max);
  }

  // 一侧能有多宽，取决于要给画面留多少：三栏时另一侧也在抢同一行空间。
  function paneWidthBounds(options = {}) {
    const min = options.minPane ?? LIMITS.min;
    const minStage = options.minStage ?? LIMITS.minStage;
    const hardMax = options.maxPane ?? LIMITS.max;
    const workbenchWidth = Number(options.workbenchWidth) || 0;
    const otherPaneWidth = Number(options.otherPaneWidth) || 0;
    return { min, max: Math.min(hardMax, workbenchWidth - otherPaneWidth - minStage) };
  }

  // 绝对量而非增量：一次拖动里反复越界再拖回来，位置能跟着指针原路返回。
  function paneWidthFromDrag(options) {
    const delta = Number(options.clientX) - Number(options.startX);
    return options.side === "right" ? Number(options.startWidth) - delta : Number(options.startWidth) + delta;
  }

  // 存储里可能出现 "320"、"abc"、null，统一收敛成数字或 null。
  function readPaneWidths(storage) {
    const out = { subtitles: null, chat: null };
    if (!storage) return out;
    let raw = null;
    try { raw = storage.getItem(STORAGE_KEY); } catch { return out; }
    if (!raw) return out;
    let parsed = null;
    try { parsed = JSON.parse(raw); } catch { return out; }
    if (!parsed || typeof parsed !== "object") return out;
    for (const edge of Object.keys(out)) {
      const value = Number(parsed[edge]);
      if (Number.isFinite(value) && value > 0) out[edge] = value;
    }
    return out;
  }

  function writePaneWidths(storage, widths) {
    if (!storage) return;
    const payload = {};
    for (const edge of Object.keys(EDGES)) {
      if (Number.isFinite(widths[edge])) payload[edge] = Math.round(widths[edge]);
    }
    try {
      if (Object.keys(payload).length === 0) storage.removeItem(STORAGE_KEY);
      else storage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch { /* 无痕模式：宽度只是偏好，丢得起 */ }
  }

  function createPaneResizer(options = {}) {
    const workbench = options.workbench;
    if (!workbench) throw new Error("createPaneResizer requires a workbench element");
    const doc = options.doc || (typeof document !== "undefined" ? document : null);
    const win = options.win || (typeof window !== "undefined" ? window : null);
    const storage = options.storage || null;
    const limits = { ...LIMITS, ...(options.limits || {}) };
    const panes = options.panes || {};
    // 用户要求的宽度（持久化用）；实际渲染宽度每次按当前视口重新收敛，
    // 所以把窗口拖窄只会临时压扁，拖回来仍然是他当初选的值。
    const requested = readPaneWidths(storage);
    const entries = [];
    let dragging = null;

    function paneFor(edge) {
      if (panes[edge]) return panes[edge];
      const id = EDGES[edge].pane;
      return doc?.getElementById ? doc.getElementById(id) : null;
    }

    function measuredWidth(edge) {
      const rect = paneFor(edge)?.getBoundingClientRect?.();
      return rect ? Math.round(rect.width) : 0;
    }

    function boundsFor(edge, otherPaneWidth) {
      return paneWidthBounds({
        workbenchWidth: Math.round(workbench.getBoundingClientRect?.().width || 0),
        otherPaneWidth: otherPaneWidth === undefined ? measuredWidth(EDGES[edge].other) : otherPaneWidth,
        minPane: limits.min,
        minStage: limits.minStage,
        maxPane: limits.max,
      });
    }

    // 隐藏栏（display:none）量出来就是 0，正好等于「不占横向空间」。
    function appliedWidth(edge, otherPaneWidth) {
      if (!Number.isFinite(requested[edge])) return null;
      return clampPaneWidth(requested[edge], boundsFor(edge, otherPaneWidth));
    }

    function visibleWidth(edge) {
      return appliedWidth(edge) ?? measuredWidth(edge);
    }

    function apply(edge, width) {
      workbench.style?.setProperty?.(EDGES[edge].variable, `${Math.round(width)}px`);
    }

    function paintAria(edge, handle, width, otherPaneWidth) {
      const range = boundsFor(edge, otherPaneWidth);
      handle?.setAttribute?.("aria-valuemin", String(range.min));
      handle?.setAttribute?.("aria-valuemax", String(Math.max(range.min, Math.round(range.max))));
      // 栏隐藏时量到 0，此时保留 HTML 里的初值，不要谎报 0
      if (Number.isFinite(width)) handle?.setAttribute?.("aria-valuenow", String(Math.round(width)));
    }

    // 只有用户真的改过宽度才走到这里 —— 写内联变量意味着脱离 CSS 默认值。
    function paint(edge, handle, width) {
      apply(edge, width);
      paintAria(edge, handle, width);
    }

    // 没有内联宽度时：几何完全交给 CSS，JS 只同步无障碍属性。
    function paintMeasured(edge, handle) {
      const measured = measuredWidth(edge);
      paintAria(edge, handle, measured > 0 ? measured : null);
    }

    function refresh(edge, handle) {
      const applied = appliedWidth(edge);
      if (applied === null) paintMeasured(edge, handle);
      else paint(edge, handle, applied);
    }

    // 先读后写：两次 getBoundingClientRect 之间不插 style 写入，避免强制同步布局。
    function reflow() {
      const snapshot = entries.map((entry) => ({
        entry,
        other: measuredWidth(EDGES[entry.edge].other),
        measured: measuredWidth(entry.edge),
      }));
      for (const { entry, other, measured } of snapshot) {
        const applied = appliedWidth(entry.edge, other);
        if (applied !== null) paint(entry.edge, entry.handle, applied);
        else paintAria(entry.edge, entry.handle, measured > 0 ? measured : null, other);
      }
    }

    function setWidth(edge, handle, width) {
      requested[edge] = clampPaneWidth(width, boundsFor(edge));
      paint(edge, handle, requested[edge]);
      writePaneWidths(storage, requested);
    }

    // 复位 = 删掉内联变量，把宽度还给 CSS 默认值（原型值）。
    function reset(edge, handle) {
      requested[edge] = null;
      workbench.style?.removeProperty?.(EDGES[edge].variable);
      writePaneWidths(storage, requested);
      paintMeasured(edge, handle);
    }

    function stopDrag(handle) {
      if (!dragging) return;
      dragging = null;
      handle?.classList?.remove?.("is-dragging");
      doc?.body?.classList?.remove?.("is-resizing-panes");
    }

    function buildHandle(edge, handle) {
      entries.push({ edge, handle });
      refresh(edge, handle);

      handle.addEventListener("pointerdown", (event) => {
        if (event.button !== undefined && event.button !== 0) return;
        if (!Number.isFinite(event.clientX)) return;
        event.preventDefault?.();
        // origin 记的是「这次拖动之前用户要求的宽度」，Esc 用它原样退回。
        dragging = { edge, startX: event.clientX, startWidth: visibleWidth(edge), origin: requested[edge] };
        handle.classList?.add?.("is-dragging");
        doc?.body?.classList?.add?.("is-resizing-panes");
        handle.setPointerCapture?.(event.pointerId);
        handle.focus?.();
      });

      handle.addEventListener("pointermove", (event) => {
        if (!dragging || dragging.edge !== edge) return;
        if (!Number.isFinite(event.clientX)) return;
        event.preventDefault?.();
        const raw = paneWidthFromDrag({
          side: EDGES[edge].side,
          startWidth: dragging.startWidth,
          startX: dragging.startX,
          clientX: event.clientX,
        });
        requested[edge] = clampPaneWidth(raw, boundsFor(edge));
        paint(edge, handle, requested[edge]);
      });

      const finish = (event) => {
        if (!dragging || dragging.edge !== edge) return;
        handle.releasePointerCapture?.(event.pointerId);
        stopDrag(handle);
        writePaneWidths(storage, requested);
        refresh(edge, handle);
      };
      handle.addEventListener("pointerup", finish);
      handle.addEventListener("pointercancel", finish);

      handle.addEventListener("dblclick", (event) => {
        event.preventDefault?.();
        reset(edge, handle);
      });

      handle.addEventListener("keydown", (event) => {
        const key = event.key;
        if (key === "Enter" || key === " ") {
          event.preventDefault?.();
          reset(edge, handle);
          return;
        }
        if (key === "Escape") {
          if (!dragging || dragging.edge !== edge) return;
          event.preventDefault?.();
          const origin = dragging.origin;
          stopDrag(handle);
          if (Number.isFinite(origin)) setWidth(edge, handle, origin);
          else reset(edge, handle);
          return;
        }
        const step = event.shiftKey ? limits.coarseStep : limits.step;
        const base = visibleWidth(edge);
        const sign = EDGES[edge].side === "right" ? -1 : 1;
        let next = null;
        if (key === "ArrowLeft") next = base - step * sign;
        else if (key === "ArrowRight") next = base + step * sign;
        else if (key === "Home") next = boundsFor(edge).min;
        else if (key === "End") next = boundsFor(edge).max;
        if (next === null) return;
        event.preventDefault?.();
        setWidth(edge, handle, next);
      });
    }

    if (options.handles) {
      for (const [edge, handle] of Object.entries(options.handles)) {
        if (handle && EDGES[edge]) buildHandle(edge, handle);
      }
    } else if (doc?.querySelectorAll) {
      for (const handle of doc.querySelectorAll("[data-pane-edge]")) {
        const edge = handle.dataset?.paneEdge;
        if (EDGES[edge]) buildHandle(edge, handle);
      }
    }

    win?.addEventListener?.("resize", reflow);

    return {
      reflow,
      reset,
      getRequested: () => ({ ...requested }),
      getApplied: (edge) => appliedWidth(edge),
      boundsFor,
      dispose() {
        win?.removeEventListener?.("resize", reflow);
        entries.length = 0;
        dragging = null;
      },
    };
  }

  const exported = {
    PANE_WIDTH_LIMITS: LIMITS,
    PANE_WIDTH_STORAGE_KEY: STORAGE_KEY,
    clampPaneWidth,
    paneWidthBounds,
    paneWidthFromDrag,
    readPaneWidths,
    writePaneWidths,
    createPaneResizer,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else Object.assign(global, exported, { LagLingoPaneResizer: exported });
})(typeof window !== "undefined" ? window : globalThis);
