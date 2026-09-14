(function (global) {
  "use strict";
  const WORKBENCH_VIEWS = ["split", "subtitles", "chat"];

  function createWorkbenchState(options = {}) {
    let view = WORKBENCH_VIEWS.includes(options.initialView) ? options.initialView : "split";
    let targetDelay = options.initialTargetDelay || 15;
    const listeners = [];
    return {
      getView: () => view,
      setView(next) {
        if (!WORKBENCH_VIEWS.includes(next)) throw new Error(`Invalid view '${next}'`);
        if (view === next) return;
        view = next;
        for (const listener of listeners) listener(view);
      },
      onViewChange(listener) { listeners.push(listener); },
      getTargetDelay: () => targetDelay,
      setTargetDelay(value) { targetDelay = value; },
    };
  }

  function createWorkbenchController(options = {}) {
    const storage = options.storage || null;
    const initialView = storage?.getItem("laglingo.workbenchView") || "split";
    const state = createWorkbenchState({ initialView });
    const render = (view) => {
      options.container?.classList.remove("view-split", "view-subtitles", "view-chat");
      options.container?.classList.add(`view-${view}`);
      for (const [key, button] of Object.entries(options.tabs || {})) {
        button?.classList.toggle("active", key === view);
        button?.setAttribute("aria-selected", key === view ? "true" : "false");
      }
      try { storage?.setItem("laglingo.workbenchView", view); } catch {}
    };
    state.onViewChange(render);
    for (const [view, button] of Object.entries(options.tabs || {})) button?.addEventListener("click", () => state.setView(view));
    render(state.getView());
    return { state, getView: state.getView, setView: state.setView };
  }

  function subtitleTimelineTiming(cueStart, offset, format = (value) => String(value)) {
    const wallTime = Number(cueStart) - Number(offset || 0);
    return { wallTime, label: format(wallTime) };
  }

  function createFollowModeController(options = {}) {
    const container = options.container;
    const button = options.button;
    let following = true;
    const reduced = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    const followBehavior = reduced || options.smoothFollow === true ? (reduced ? "auto" : "smooth") : "auto";
    const sync = () => {
      button?.classList.toggle("active", following);
      if (button) button.textContent = following ? "跟随当前" : "回到当前";
    };
    const pause = () => { following = false; sync(); };
    for (const event of ["wheel", "touchstart", "keydown"]) container?.addEventListener(event, pause, { passive: true });
    container?.addEventListener("scroll", () => {
      const distance = container.scrollHeight - container.scrollTop - container.clientHeight;
      if (distance <= 24) { following = true; sync(); }
    }, { passive: true });
    button?.addEventListener("click", () => {
      following = true;
      container?.scrollTo({ top: container.scrollHeight, behavior: followBehavior });
      sync();
    });
    sync();
    return {
      isFollowing: () => following,
      onUserScroll({ isAtBottom, distanceToBottom = 0 }) { following = Boolean(isAtBottom || distanceToBottom <= 24); sync(); return following; },
      pauseFollow: pause,
      resumeFollow() { following = true; sync(); return following; },
      onNewContent() { if (following) container?.scrollTo({ top: container.scrollHeight, behavior: followBehavior }); },
    };
  }

  const exported = { WORKBENCH_VIEWS, createWorkbenchState, createWorkbenchController, createFollowModeController, subtitleTimelineTiming };
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  else Object.assign(global, exported, { LagLingoWorkbench: exported });
})(typeof window !== "undefined" ? window : globalThis);
