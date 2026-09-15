((root, factory) => {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.createSubtitleWindowController = api.createSubtitleWindowController;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  const STORAGE_KEY = "lingerlens.subtitle.window.v1";
  /* 默认位置/配色取自设计原型：字幕落在画面底部（原型 bottom: 12%），
     原文柠檬黄、译文纯白。y 是窗口中心相对舞台高度的比例；0.82 让典型
     的一到两行字幕（含内边距约 50-90px）正好落在控制条上方。 */
  const DEFAULTS = Object.freeze({
    x: 0.5,
    y: 0.82,
    opacity: 0.92,
    scale: 1,
    sourceColor: "#FFD23F",
    translationColor: "#FFFFFF",
  });
  const COLOR = /^#[0-9a-f]{6}$/i;
  const number = (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const between = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));

  function sanitize(raw) {
    if (!raw || typeof raw !== "object") return { ...DEFAULTS };
    const valid = number(raw.x, -1) >= 0 && number(raw.x, 2) <= 1
      && number(raw.y, -1) >= 0 && number(raw.y, 2) <= 1
      && number(raw.opacity, 0) >= 0.2 && number(raw.opacity, 2) <= 1
      && number(raw.scale, 0) >= 0.7 && number(raw.scale, 3) <= 1.6
      && COLOR.test(String(raw.sourceColor || ""))
      && COLOR.test(String(raw.translationColor || ""));
    if (!valid) return { ...DEFAULTS };
    return {
      x: Number(raw.x), y: Number(raw.y), opacity: Number(raw.opacity), scale: Number(raw.scale),
      sourceColor: raw.sourceColor, translationColor: raw.translationColor,
    };
  }

  function createSubtitleWindowController({ stage, windowElement, storage, fullscreenDocument } = {}) {
    let prefs;
    try { prefs = sanitize(JSON.parse(storage?.getItem(STORAGE_KEY) || "null")); }
    catch { prefs = { ...DEFAULTS }; }

    function bounds() {
      const width = Math.max(1, Number(stage?.clientWidth) || 1);
      const height = Math.max(1, Number(stage?.clientHeight) || 1);
      const scale = between(number(prefs.scale, DEFAULTS.scale), 0.7, 1.6);
      return {
        maxX: Math.max(0.5, 1 - ((Number(windowElement?.offsetWidth) || 0) * scale) / (2 * width)),
        maxY: Math.max(0.5, 1 - ((Number(windowElement?.offsetHeight) || 0) * scale) / (2 * height)),
      };
    }

    function save() {
      storage?.setItem(STORAGE_KEY, JSON.stringify(prefs));
    }

    function apply({ persist = false } = {}) {
      const { maxX, maxY } = bounds();
      prefs.x = between(prefs.x, 1 - maxX, maxX);
      prefs.y = between(prefs.y, 1 - maxY, maxY);
      if (windowElement?.style) {
        windowElement.style.left = `${prefs.x * 100}%`;
        windowElement.style.top = `${prefs.y * 100}%`;
        windowElement.style.setProperty?.("--subtitle-opacity", String(prefs.opacity));
        windowElement.style.setProperty?.("--subtitle-scale", String(prefs.scale));
        windowElement.style.setProperty?.("--subtitle-source-color", prefs.sourceColor);
        windowElement.style.setProperty?.("--subtitle-translation-color", prefs.translationColor);
      }
      if (persist) save();
      return { ...prefs };
    }

    function moveTo(x, y) {
      prefs.x = number(x, DEFAULTS.x);
      prefs.y = number(y, DEFAULTS.y);
      return apply({ persist: true });
    }

    function nudge(key, largeStep) {
      const delta = largeStep ? 0.1 : 0.02;
      const directions = { ArrowLeft: [-delta, 0], ArrowRight: [delta, 0], ArrowUp: [0, -delta], ArrowDown: [0, delta] };
      if (!directions[key]) return false;
      return Boolean(moveTo(prefs.x + directions[key][0], prefs.y + directions[key][1]));
    }

    function updateStyle(next) {
      prefs.opacity = between(number(next.opacity, prefs.opacity), 0.2, 1);
      prefs.scale = between(number(next.scale, prefs.scale), 0.7, 1.6);
      if (COLOR.test(String(next.sourceColor || ""))) prefs.sourceColor = next.sourceColor;
      if (COLOR.test(String(next.translationColor || ""))) prefs.translationColor = next.translationColor;
      return apply({ persist: true });
    }

    function reset() {
      prefs = { ...DEFAULTS };
      return apply({ persist: true });
    }

    async function toggleFullscreen(doc = fullscreenDocument || (typeof document !== "undefined" ? document : null)) {
      if (doc?.fullscreenElement) return doc.exitFullscreen?.();
      return stage?.requestFullscreen?.();
    }

    apply();
    return { apply, moveTo, nudge, updateStyle, reset, toggleFullscreen, preferences: () => ({ ...prefs }) };
  }

  return { createSubtitleWindowController };
});
