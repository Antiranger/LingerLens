const test = require("node:test");
const assert = require("node:assert/strict");

const { playerContext, stubElement, spy } = require("./player-harness.js");

test("the update button re-localizes when the locale changes without a new update status", () => {
  const elements = new Map();
  const el = (id) => {
    if (!elements.has(id)) elements.set(id, stubElement());
    return elements.get(id);
  };
  const labels = { current: "已是最新版本", currentEn: "Up to date" };
  let english = false;
  const h = playerContext({
    state: { lastUpdateSignature: null, updateProgressTimer: null, buildIdentity: null },
    globals: {
      el,
      updateLabel: (_key, fallback) => english && fallback === labels.current ? labels.currentEn : fallback,
      diagnosticsBar: { push: spy() },
      setInterval: () => 1,
      clearInterval: spy(),
    },
    functions: ["describeBuild", "stopUpdateProgress", "renderUpdate"],
  });
  const current = { version: "0.1.0", packaged: true, update: { status: "current" } };

  h.call("renderUpdate", current);
  assert.equal(el("appUpdateButton").textContent, labels.current);

  english = true;
  h.call("renderUpdate", current, true);
  assert.equal(el("appUpdateButton").textContent, labels.currentEn);
});
