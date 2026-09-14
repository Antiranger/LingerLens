const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  createWorkbenchState,
  subtitleTimelineTiming,
  WORKBENCH_VIEWS,
} = require(path.resolve(__dirname, "../web-player/workbench-controller.js"));

test("WorkbenchState manages tab selection and view transitions", () => {
  const state = createWorkbenchState({ initialView: "split" });
  assert.equal(state.getView(), "split");

  let notifiedView = null;
  state.onViewChange((view) => {
    notifiedView = view;
  });

  state.setView("chat");
  assert.equal(state.getView(), "chat");
  assert.equal(notifiedView, "chat");

  state.setView("subtitles");
  assert.equal(state.getView(), "subtitles");
  assert.equal(notifiedView, "subtitles");

  // Invalid view should throw or be ignored
  assert.throws(() => state.setView("invalid"));
});

test("subtitle timeline timing immediately reflects offset changes for display and seek", () => {
  const format = (value) => `T${value.toFixed(1)}`;
  const initial = subtitleTimelineTiming(100, 0, format);
  const shifted = subtitleTimelineTiming(100, 1.5, format);
  assert.deepEqual(initial, { wallTime: 100, label: "T100.0" });
  assert.deepEqual(shifted, { wallTime: 98.5, label: "T98.5" });
});

test("FollowModeController uses immediate positioning under sustained updates", () => {
  const events = {};
  const calls = [];
  const container = {
    scrollHeight: 1000,
    scrollTop: 0,
    clientHeight: 300,
    addEventListener(name, callback) { events[name] = callback; },
    scrollTo(options) { calls.push(options); },
  };
  const { createFollowModeController } = require(path.resolve(__dirname, "../web-player/workbench-controller.js"));
  const controller = createFollowModeController({ container });
  controller.onNewContent();
  assert.deepEqual(calls, [{ top: 1000, behavior: "auto" }]);
});

test("WorkbenchState preserves target delay and active subtitle state across view switches", () => {
  const state = createWorkbenchState();
  state.setTargetDelay(7.5);
  assert.equal(state.getTargetDelay(), 7.5);

  state.setView("chat");
  assert.equal(state.getTargetDelay(), 7.5);

  state.setView("split");
  assert.equal(state.getTargetDelay(), 7.5);
});
