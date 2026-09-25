/* Frame-clock subtitle painting. No positive lookahead or wall-clock guessing. */
(function (global) {
  "use strict";
  function createSubtitleRenderLoop(options) {
    const video = options.video;
    const render = options.render;
    const now = options.now || (() => performance.now());
    const hidden = options.isHidden || (() => document.hidden);
    const every = options.setInterval || setInterval;
    const cancel = options.clearInterval || clearInterval;
    let stopped = false, frameId = null, lastFrameAt = -Infinity;
    function frame(_now, metadata) {
      if (stopped) return;
      lastFrameAt = now();
      if (!hidden()) render(Number.isFinite(metadata.mediaTime) ? metadata.mediaTime : undefined);
      if (!stopped) frameId = video.requestVideoFrameCallback(frame);
    }
    if (typeof video.requestVideoFrameCallback === "function") {
      frameId = video.requestVideoFrameCallback(frame);
    }
    const timer = every(() => {
      options.tick?.();
      // Paused, stalled and older browsers still refresh arriving translations.
      if (!stopped && !hidden() && now() - lastFrameAt >= 200) render();
    }, 100);
    return { stop() { stopped = true; cancel(timer);
      if (frameId !== null) video.cancelVideoFrameCallback?.(frameId); } };
  }
  global.createSubtitleRenderLoop = createSubtitleRenderLoop;
  if (typeof module !== "undefined") module.exports = { createSubtitleRenderLoop };
})(typeof window !== "undefined" ? window : globalThis);
