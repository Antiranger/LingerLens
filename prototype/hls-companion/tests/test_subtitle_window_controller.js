const test = require("node:test");
const assert = require("node:assert/strict");

const { createSubtitleWindowController } = require("../web-player/subtitle-window-controller.js");

function fixture(saved = null) {
  const values = new Map(saved ? [["lingerlens.subtitle.window.v1", JSON.stringify(saved)]] : []);
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  const stage = {
    clientWidth: 1000,
    clientHeight: 500,
    requested: false,
    requestFullscreen() { this.requested = true; return Promise.resolve(); },
  };
  const windowElement = {
    offsetWidth: 400,
    offsetHeight: 120,
    style: { setProperty(name, value) { this[name] = value; } },
    dataset: {},
  };
  return { storage, stage, windowElement, values };
}

test("LingerLens fullscreen requests the stage that owns video and subtitles", async () => {
  const { storage, stage, windowElement } = fixture();
  const controller = createSubtitleWindowController({ stage, windowElement, storage });
  await controller.toggleFullscreen({ fullscreenElement: null });
  assert.equal(stage.requested, true);
});

test("subtitle window restores safe preferences, clamps position, and persists normalized coordinates", () => {
  const { storage, stage, windowElement, values } = fixture({
    x: 4,
    y: -2,
    opacity: 3,
    scale: 0,
    sourceColor: "not-a-color",
    translationColor: "#00ff66",
  });
  const controller = createSubtitleWindowController({ stage, windowElement, storage });
  // Defaults are the design prototype's: subtitle window near the bottom of the
  // frame (prototype/redesign/style.css:625 `bottom: 12%`), source line in the
  // prototype's #FFD23F, translation line white.
  assert.deepEqual(controller.preferences(), {
    x: 0.5,
    y: 0.82,
    opacity: 0.92,
    scale: 1,
    sourceColor: "#FFD23F",
    translationColor: "#FFFFFF",
  });

  controller.moveTo(0.95, 0.95);
  const moved = controller.preferences();
  assert.equal(moved.x, 0.8);
  assert.equal(moved.y, 0.88);
  assert.deepEqual(JSON.parse(values.get("lingerlens.subtitle.window.v1")), moved);
});

test("keyboard movement, reset, opacity, scale, and colors update the public preferences", () => {
  const { storage, stage, windowElement } = fixture();
  const controller = createSubtitleWindowController({ stage, windowElement, storage });
  assert.equal(controller.nudge("ArrowLeft", false), true);
  assert.equal(controller.preferences().x, 0.48);
  assert.equal(controller.nudge("ArrowUp", true), true);
  assert.equal(controller.preferences().y, 0.72);
  assert.equal(controller.nudge("Enter", false), false);

  controller.moveTo(0.95, 0.95);
  controller.updateStyle({ opacity: 0.55, scale: 1.4, sourceColor: "#123456", translationColor: "#abcdef" });
  assert.equal(controller.preferences().x, 0.72);
  assert.equal(controller.preferences().y, 0.832);
  assert.equal(windowElement.style["--subtitle-opacity"], "0.55");
  assert.equal(windowElement.style["--subtitle-scale"], "1.4");
  assert.equal(windowElement.style["--subtitle-source-color"], "#123456");
  assert.equal(windowElement.style["--subtitle-translation-color"], "#abcdef");

  controller.reset();
  assert.equal(controller.preferences().x, 0.5);
  assert.equal(controller.preferences().y, 0.82);
});
