const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  PANE_WIDTH_LIMITS,
  PANE_WIDTH_STORAGE_KEY,
  clampPaneWidth,
  paneWidthBounds,
  paneWidthFromDrag,
  readPaneWidths,
  writePaneWidths,
  createPaneResizer,
} = require(path.resolve(__dirname, "../web-player/pane-resizer.js"));

/* ── 最小但诚实的 DOM 替身：事件真的被记录并派发，样式真的被存下来 ── */

function createStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: (key) => map.delete(key),
    dump: () => Object.fromEntries(map),
  };
}

function createClassList() {
  const set = new Set();
  return {
    add: (...names) => names.forEach((name) => set.add(name)),
    remove: (...names) => names.forEach((name) => set.delete(name)),
    contains: (name) => set.has(name),
    toggle: (name, force) => (force ? set.add(name) : set.delete(name)),
    values: () => [...set],
  };
}

function createElement(options = {}) {
  const listeners = {};
  const attributes = { ...(options.attrs || {}) };
  const styles = {};
  const element = {
    id: options.id || "",
    dataset: options.dataset || {},
    attrs: attributes,
    styles,
    classList: createClassList(),
    focused: false,
    captured: [],
    released: [],
    addEventListener(name, callback) {
      (listeners[name] = listeners[name] || []).push(callback);
    },
    dispatch(name, event = {}) {
      const payload = { pointerId: 1, button: 0, preventDefault() {}, ...event };
      for (const callback of listeners[name] || []) callback(payload);
      return payload;
    },
    hasListener: (name) => Boolean(listeners[name]?.length),
    setAttribute: (name, value) => { attributes[name] = String(value); },
    getAttribute: (name) => (name in attributes ? attributes[name] : null),
    setPointerCapture(id) { this.captured.push(id); },
    releasePointerCapture(id) { this.released.push(id); },
    focus() { this.focused = true; },
    style: {
      setProperty: (name, value) => { styles[name] = value; },
      removeProperty: (name) => { delete styles[name]; },
      getPropertyValue: (name) => styles[name] || "",
    },
    getBoundingClientRect: () => ({ width: options.width ?? 0, height: 620, x: 0, y: 0 }),
  };
  return element;
}

function createHarness(options = {}) {
  const paneWidths = { subtitles: options.subtitlesWidth ?? 320, chat: options.chatWidth ?? 320 };
  const workbench = createElement({ width: options.workbenchWidth ?? 2000 });
  const panes = {
    subtitles: createElement({ id: "paneSubtitles", width: paneWidths.subtitles }),
    chat: createElement({ id: "paneChat", width: paneWidths.chat }),
  };
  const handles = {
    // 初值照抄 index.html：JS 没跑起来时屏幕阅读器读到的就是这些
    subtitles: createElement({
      dataset: { paneEdge: "subtitles" },
      attrs: { role: "separator", tabindex: "0", "aria-valuemin": "280", "aria-valuemax": "560", "aria-valuenow": "320" },
    }),
    chat: createElement({
      dataset: { paneEdge: "chat" },
      attrs: { role: "separator", tabindex: "0", "aria-valuemin": "280", "aria-valuemax": "560", "aria-valuenow": "320" },
    }),
  };
  const body = { classList: createClassList() };
  const doc = {
    body,
    getElementById: (id) => Object.values(panes).find((pane) => pane.id === id) || null,
    querySelectorAll: (selector) =>
      selector === "[data-pane-edge]" ? Object.values(handles) : [],
  };
  const winListeners = {};
  const win = {
    addEventListener: (name, callback) => { (winListeners[name] = winListeners[name] || []).push(callback); },
    removeEventListener: (name, callback) => {
      winListeners[name] = (winListeners[name] || []).filter((item) => item !== callback);
    },
    resize: () => (winListeners.resize || []).forEach((callback) => callback({})),
    listenerCount: (name) => (winListeners[name] || []).length,
  };
  const storage = options.storage ?? createStorage();
  return { workbench, panes, handles, body, doc, win, storage, paneWidths };
}

/* ── 纯函数：夹取、边界、拖动方向、存储解析 ── */

test("clampPaneWidth keeps a width inside its bounds and rounds it", () => {
  const bounds = { min: 280, max: 560 };
  assert.equal(clampPaneWidth(400.6, bounds), 401);
  assert.equal(clampPaneWidth(100, bounds), 280);
  assert.equal(clampPaneWidth(9000, bounds), 560);
  // 视口太窄时 max 会小于 min，此时以 min 为准而不是把两者对调
  assert.equal(clampPaneWidth(500, { min: 280, max: 100 }), 280);
  assert.equal(clampPaneWidth(Number.NaN, bounds), 280);
});

test("paneWidthBounds reserves room for the video before growing a sidebar", () => {
  // 三栏：另一侧也在同一行抢空间
  assert.deepEqual(
    paneWidthBounds({ workbenchWidth: 2000, otherPaneWidth: 320, minStage: 320, maxPane: 560 }),
    { min: 280, max: 560 },
  );
  // 窄屏：给画面留够 320 之后只剩 400
  assert.deepEqual(
    paneWidthBounds({ workbenchWidth: 1040, otherPaneWidth: 320, minStage: 320, maxPane: 560 }),
    { min: 280, max: 400 },
  );
  // 单栏视图另一侧隐藏，量到 0
  assert.deepEqual(
    paneWidthBounds({ workbenchWidth: 1000, otherPaneWidth: 0, minStage: 320, maxPane: 560 }),
    { min: 280, max: 560 },
  );
});

test("paneWidthFromDrag is absolute, so a sidebar dragged past its limit comes back", () => {
  const base = { startWidth: 320, startX: 1000 };
  // 左侧栏：指针右移变宽
  assert.equal(paneWidthFromDrag({ ...base, side: "left", clientX: 1060 }), 380);
  assert.equal(paneWidthFromDrag({ ...base, side: "left", clientX: 900 }), 220);
  // 右侧栏：指针右移变窄
  assert.equal(paneWidthFromDrag({ ...base, side: "right", clientX: 1060 }), 260);
  assert.equal(paneWidthFromDrag({ ...base, side: "right", clientX: 900 }), 420);
  // 从起点重新计算，不受中途被夹取的影响
  assert.equal(paneWidthFromDrag({ ...base, side: "left", clientX: 1120 }), 440);
});

test("readPaneWidths survives every shape of junk the storage can hold", () => {
  assert.deepEqual(readPaneWidths(null), { subtitles: null, chat: null });
  assert.deepEqual(readPaneWidths(createStorage()), { subtitles: null, chat: null });
  assert.deepEqual(readPaneWidths(createStorage({ [PANE_WIDTH_STORAGE_KEY]: "{not json" })),
    { subtitles: null, chat: null });
  assert.deepEqual(readPaneWidths(createStorage({ [PANE_WIDTH_STORAGE_KEY]: "42" })),
    { subtitles: null, chat: null });
  assert.deepEqual(readPaneWidths(createStorage({ [PANE_WIDTH_STORAGE_KEY]: '{"subtitles":"380"}' })),
    { subtitles: 380, chat: null });
  assert.deepEqual(readPaneWidths(createStorage({ [PANE_WIDTH_STORAGE_KEY]: '{"subtitles":-5,"chat":0}' })),
    { subtitles: null, chat: null });
  const hostile = { getItem() { throw new Error("SecurityError"); } };
  assert.deepEqual(readPaneWidths(hostile), { subtitles: null, chat: null });
});

test("writePaneWidths drops nulls and removes the key once nothing is customised", () => {
  const storage = createStorage();
  writePaneWidths(storage, { subtitles: 380, chat: null });
  assert.deepEqual(storage.dump(), { [PANE_WIDTH_STORAGE_KEY]: '{"subtitles":380}' });
  writePaneWidths(storage, { subtitles: 380, chat: 300.4 });
  assert.deepEqual(storage.dump(), { [PANE_WIDTH_STORAGE_KEY]: '{"subtitles":380,"chat":300}' });
  writePaneWidths(storage, { subtitles: null, chat: null });
  assert.deepEqual(storage.dump(), {});
  // 无痕模式会抛，不能把异常带出去
  const hostile = { setItem() { throw new Error("QuotaExceededError"); }, removeItem() { throw new Error("nope"); } };
  assert.doesNotThrow(() => writePaneWidths(hostile, { subtitles: 380, chat: null }));
});

/* ── 控制器：初值、拖动、键盘、复位、重排 ── */

test("PaneResizer writes nothing at rest so the prototype layout is untouched", () => {
  const harness = createHarness();
  createPaneResizer({ ...harness, storage: harness.storage });
  // 关键回归点：没有历史偏好时必须一个内联变量都不写
  assert.deepEqual(harness.workbench.styles, {});
  assert.equal(harness.handles.subtitles.getAttribute("aria-valuenow"), "320");
  assert.equal(harness.handles.chat.getAttribute("aria-valuenow"), "320");
  assert.equal(harness.handles.subtitles.getAttribute("role"), "separator"); // role 由 HTML 提供，JS 不重复写
});

test("PaneResizer restores a stored width on load", () => {
  const storage = createStorage({ [PANE_WIDTH_STORAGE_KEY]: '{"subtitles":420,"chat":260}' });
  const harness = createHarness({ storage });
  createPaneResizer({ ...harness, storage });
  assert.equal(harness.workbench.styles["--pane-left-w"], "420px");
  // 260 是下限提高之前存下的值：载入时必须被抬到当前下限，而不是原样套用。
  // 低于下限的侧栏装不下自己的面板头（徽章 + 标题 + 跟随按钮）。
  assert.equal(harness.workbench.styles["--pane-right-w"], "280px");
  assert.equal(harness.handles.subtitles.getAttribute("aria-valuenow"), "420");
  assert.equal(harness.handles.chat.getAttribute("aria-valuenow"), "280");
});

test("a drag resizes the pane and persists on release", () => {
  const harness = createHarness();
  const storage = harness.storage;
  createPaneResizer({ ...harness, storage });

  harness.handles.subtitles.dispatch("pointerdown", { clientX: 1000 });
  assert.equal(harness.handles.subtitles.captured.length, 1);
  assert.equal(harness.body.classList.contains("is-resizing-panes"), true);
  assert.equal(harness.handles.subtitles.classList.contains("is-dragging"), true);

  harness.handles.subtitles.dispatch("pointermove", { clientX: 1080 });
  assert.equal(harness.workbench.styles["--pane-left-w"], "400px");
  // 拖动过程中不该写盘，松手才落盘
  assert.equal(storage.getItem(PANE_WIDTH_STORAGE_KEY), null);

  harness.handles.subtitles.dispatch("pointerup", { clientX: 1080 });
  assert.equal(harness.body.classList.contains("is-resizing-panes"), false);
  assert.equal(harness.handles.subtitles.classList.contains("is-dragging"), false);
  assert.equal(storage.getItem(PANE_WIDTH_STORAGE_KEY), '{"subtitles":400}');
  assert.equal(harness.handles.subtitles.getAttribute("aria-valuenow"), "400");
});

test("the right-hand pane grows when dragged left, and the stage keeps its floor", () => {
  const harness = createHarness({ workbenchWidth: 1040, chatWidth: 320 });
  createPaneResizer({ ...harness, storage: harness.storage });
  // workbench 1040 - 左栏 320 - 画面 320 = 400 是上限
  harness.handles.chat.dispatch("pointerdown", { clientX: 700 });
  harness.handles.chat.dispatch("pointermove", { clientX: 0 });
  assert.equal(harness.workbench.styles["--pane-right-w"], "400px");
  assert.equal(harness.handles.chat.getAttribute("aria-valuemax"), "400");
  // 把指针推回去（绝对值计算），宽度跟着回到指针位置
  harness.handles.chat.dispatch("pointermove", { clientX: 640 });
  assert.equal(harness.workbench.styles["--pane-right-w"], "380px");
});

test("double-click and Enter both hand the width back to the CSS default", () => {
  const storage = createStorage({ [PANE_WIDTH_STORAGE_KEY]: '{"subtitles":420}' });
  const harness = createHarness({ storage });
  createPaneResizer({ ...harness, storage });
  assert.equal(harness.workbench.styles["--pane-left-w"], "420px");

  harness.handles.subtitles.dispatch("dblclick", {});
  assert.deepEqual(harness.workbench.styles, {});
  assert.deepEqual(storage.dump(), {});
  assert.equal(harness.handles.subtitles.getAttribute("aria-valuenow"), "320");

  harness.handles.subtitles.dispatch("keydown", { key: "ArrowRight" });
  assert.equal(harness.workbench.styles["--pane-left-w"], "336px");
  harness.handles.subtitles.dispatch("keydown", { key: "Enter" });
  assert.deepEqual(harness.workbench.styles, {});
});

test("keyboard steps follow the pane's own side, and Home/End jump to the limits", () => {
  const harness = createHarness({ workbenchWidth: 1040 });
  createPaneResizer({ ...harness, storage: harness.storage });

  // 左栏在左边：ArrowRight 变宽
  harness.handles.subtitles.dispatch("keydown", { key: "ArrowRight" });
  assert.equal(harness.workbench.styles["--pane-left-w"], "336px");
  harness.handles.subtitles.dispatch("keydown", { key: "ArrowLeft" });
  assert.equal(harness.workbench.styles["--pane-left-w"], "320px");
  // Shift 是粗调
  harness.handles.subtitles.dispatch("keydown", { key: "ArrowRight", shiftKey: true });
  assert.equal(harness.workbench.styles["--pane-left-w"], "384px");
  assert.equal(harness.handles.subtitles.getAttribute("aria-valuemax"), "400");
  harness.handles.subtitles.dispatch("keydown", { key: "End" });
  assert.equal(harness.workbench.styles["--pane-left-w"], "400px");
  harness.handles.subtitles.dispatch("keydown", { key: "Home" });
  assert.equal(harness.workbench.styles["--pane-left-w"], "280px");

  // 右栏在右边：ArrowLeft 变宽
  harness.handles.chat.dispatch("keydown", { key: "ArrowLeft" });
  assert.equal(harness.workbench.styles["--pane-right-w"], "336px");
});

test("Escape mid-drag returns to the width the user had asked for", () => {
  const storage = createStorage({ [PANE_WIDTH_STORAGE_KEY]: '{"subtitles":400}' });
  const harness = createHarness({ storage });
  createPaneResizer({ ...harness, storage });

  harness.handles.subtitles.dispatch("pointerdown", { clientX: 1000 });
  harness.handles.subtitles.dispatch("pointermove", { clientX: 1100 });
  assert.equal(harness.workbench.styles["--pane-left-w"], "500px");
  harness.handles.subtitles.dispatch("keydown", { key: "Escape" });
  assert.equal(harness.workbench.styles["--pane-left-w"], "400px");
  assert.equal(harness.body.classList.contains("is-resizing-panes"), false);
  assert.equal(storage.getItem(PANE_WIDTH_STORAGE_KEY), '{"subtitles":400}');
});

test("Escape without a previous custom width clears the inline variable", () => {
  const harness = createHarness();
  createPaneResizer({ ...harness, storage: harness.storage });
  harness.handles.subtitles.dispatch("pointerdown", { clientX: 1000 });
  harness.handles.subtitles.dispatch("pointermove", { clientX: 1100 });
  harness.handles.subtitles.dispatch("keydown", { key: "Escape" });
  assert.deepEqual(harness.workbench.styles, {});
});

test("a reflow compresses for a narrow viewport without forgetting the chosen width", () => {
  let workbenchWidth = 2000;
  const harness = createHarness({ workbenchWidth });
  harness.workbench.getBoundingClientRect = () => ({ width: workbenchWidth, height: 620, x: 0, y: 0 });
  const controller = createPaneResizer({ ...harness, storage: harness.storage });

  controller.reset("subtitles", harness.handles.subtitles);
  harness.handles.subtitles.dispatch("pointerdown", { clientX: 1000 });
  harness.handles.subtitles.dispatch("pointermove", { clientX: 1200 });
  harness.handles.subtitles.dispatch("pointerup", { clientX: 1200 });
  assert.equal(harness.workbench.styles["--pane-left-w"], "520px");
  assert.equal(controller.getRequested().subtitles, 520);

  workbenchWidth = 1040;
  harness.win.resize();
  assert.equal(harness.workbench.styles["--pane-left-w"], "400px");
  assert.equal(harness.handles.subtitles.getAttribute("aria-valuenow"), "400");
  // 用户的选择没有被压扁这件事改写
  assert.equal(controller.getRequested().subtitles, 520);

  workbenchWidth = 2000;
  harness.win.resize();
  assert.equal(harness.workbench.styles["--pane-left-w"], "520px");
});

test("a hidden pane keeps its last announced width instead of reporting zero", () => {
  const harness = createHarness({ chatWidth: 0 });
  createPaneResizer({ ...harness, storage: harness.storage });
  assert.equal(harness.handles.chat.getAttribute("aria-valuenow"), "320");
  harness.win.resize();
  assert.equal(harness.handles.chat.getAttribute("aria-valuenow"), "320");
});

test("the data-attribute discovery path finds both handles and dispose unhooks resize", () => {
  const harness = createHarness();
  const controller = createPaneResizer({ ...harness, storage: harness.storage });
  assert.equal(harness.win.listenerCount("resize"), 1);
  assert.equal(harness.handles.chat.hasListener("pointerdown"), true);
  controller.dispose();
  assert.equal(harness.win.listenerCount("resize"), 0);
});

test("a missing workbench is a programming error, not a silent no-op", () => {
  assert.throws(() => createPaneResizer({ workbench: null }), /requires a workbench element/);
});

test("PaneResizer does not fight the default when a pointerdown never moves", () => {
  const harness = createHarness();
  createPaneResizer({ ...harness, storage: harness.storage });
  harness.handles.subtitles.dispatch("pointerdown", { clientX: 500 });
  harness.handles.subtitles.dispatch("pointerup", { clientX: 500 });
  // 点了但没拖：不应留下任何内联宽度，也就不会脱离原型默认值
  assert.deepEqual(harness.workbench.styles, {});
  assert.equal(PANE_WIDTH_LIMITS.min, 280);
});
